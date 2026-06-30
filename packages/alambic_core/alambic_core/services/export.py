"""alambic_core.services.export — export des documents validés.

Porté de FlowerScan (document_export), adapté à Alambic et débarrassé de l'export
interne propriétaire (FlowerExporter), remplacé par un web service standard.

Deux destinations (`way_out`) :
  - "WS"  : POST multipart vers une URL — le PDF en pièce binaire + le JSON des
            index en seconde partie. Le plus robuste (binaire natif, pas de
            base64, séparation fichier/métadonnées). Auth Bearer ou Basic.
  - "S3"  : upload vers un bucket S3 *tiers* du client (credentials sortants),
            le PDF et le JSON côte à côte.

Garde-fous de robustesse repris de FlowerScan :
  - idempotence : un document déjà exporté n'est pas réexporté ;
  - le statut ne passe à EXPORTED qu'APRÈS confirmation de l'upload ;
  - retry avec backoff sur erreurs transitoires (réseau/timeout) ;
  - le payload inclut les index validés + métadonnées du document.

Ce module construit le payload et expose les stratégies. La tâche worker
(alambic_workers.tasks.export) orchestre statut + persistance.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Erreurs réseau considérées comme transitoires (→ retry).
_TRANSIENT = (TimeoutError, ConnectionError)


@dataclass
class ExportConfig:
    """Paramètres d'export résolus depuis la Config (non-secret + secrets déchiffrés)."""

    way_out: str = ""  # "WS" | "S3"
    # Web service.
    ws_url: str = ""
    ws_auth_type: str = ""  # "" | "bearer" | "basic"
    ws_token: str = ""  # bearer token ou "user:password" pour basic
    ws_timeout: int = 60
    ws_allowed_domains: list = field(default_factory=list)  # allowlist anti-SSRF (compte)
    # S3 sortant (client).
    s3_bucket_out: str = ""
    s3_prefix_out: str = ""
    s3_region_out: str = ""
    s3_access_key_id_out: str = ""
    s3_secret_access_key_out: str = ""
    s3_endpoint_out: str = ""  # optionnel (S3 compatible non-AWS)
    # Commun.
    max_retries: int = 3


@dataclass
class ExportResult:
    ok: bool = False
    skipped: bool = False  # déjà exporté (idempotence)
    error: str = ""
    detail: dict = field(default_factory=dict)


def build_payload(document, transaction_fields: list | None = None) -> str:
    """Construit le JSON d'export d'un document (index validés + métadonnées).

    Reprend la structure FlowerScan : id, date de création, index extraits, et
    champs hérités de la transaction (contexte email, valeurs calculées, données
    d'enrichissement WS). Les index `extracted` vont dans `indexes` ; les index
    `metadata` (champs propagés + enrichissement) vont dans `transaction_fields`.
    """
    doc_indexes = getattr(document, "indexes", []) or []

    indexes = [
        {
            "name": idx.index_name,
            "value": idx.index_value,
            "score": idx.index_score,
        }
        for idx in doc_indexes
        if getattr(idx, "index_type", "") == "extracted"
    ]

    # Champs hérités/propagés/enrichis : index de type metadata du document.
    # On les expose dans transaction_fields (sémantique FlowerScan). Un override
    # explicite (paramètre) prime, sinon on dérive depuis les index metadata.
    if transaction_fields is None:
        transaction_fields = [
            {"name": idx.index_name, "value": idx.index_value}
            for idx in doc_indexes
            if getattr(idx, "index_type", "") == "metadata"
        ]

    created = getattr(document, "created_at", None)
    payload = {
        "doc_id": document.id,
        "doctype": getattr(document, "doctype", "") or "",
        "creation_date": created.isoformat() if created else None,
        "indexes": indexes,
        "transaction_fields": transaction_fields or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _with_retry(fn, max_retries: int, label: str):
    """Exécute fn avec backoff exponentiel sur erreurs transitoires."""
    last = None
    for attempt in range(max_retries):
        try:
            return fn()
        except _TRANSIENT as ex:
            last = ex
            wait = 2**attempt
            logger.warning(
                "Export %s tentative %d échouée (%s), retry dans %ds", label, attempt, ex, wait
            )
            time.sleep(wait)
        except Exception:  # noqa: BLE001
            raise  # erreur non transitoire → propager immédiatement
    raise last if last else RuntimeError(f"Export {label} échoué")


def export_config_from_config(config, secret_provider=None, allowed_domains=None) -> ExportConfig:
    """Construit une ExportConfig depuis une Config Alambic.

    Les paramètres non-secrets (way_out, URL, bucket, préfixe, région, robustesse)
    sont dans le bloc `ws`. Les secrets sont dans des colonnes chiffrées (déchiffrées
    automatiquement à la lecture) : `aws_out_enc` (JSON {access_key_id,
    secret_access_key}) pour S3 sortant, `flower_enc` (JSON {token|user|password|
    api_key}) pour l'auth du web service. `allowed_domains` est l'allowlist
    anti-SSRF du compte (account.enrich_allowed_domains, déjà parsée).
    """
    import json

    ws = config.ws or {}

    def _secret_json(raw: str) -> dict:
        # Les colonnes EncryptedString sont déjà déchiffrées à la lecture.
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    aws_out = _secret_json(getattr(config, "aws_out_enc", "") or "")
    ws_auth = _secret_json(getattr(config, "flower_enc", "") or "")

    # Token d'auth WS : selon le type, on prend le bon credential.
    auth_type = ws.get("export_auth_type", "") or ""
    ws_token = ws_auth.get("token", "") or ws_auth.get("api_key", "") or ""

    return ExportConfig(
        way_out=ws.get("way_out", "") or "",
        ws_url=ws.get("export_url", "") or "",
        ws_auth_type=auth_type,
        ws_token=ws_token,
        ws_timeout=int(ws.get("export_timeout", 60) or 60),
        ws_allowed_domains=allowed_domains or [],
        s3_bucket_out=ws.get("s3_bucket_out", "") or "",
        s3_prefix_out=ws.get("s3_prefix_out", "") or "",
        s3_region_out=ws.get("aws_region_out", "") or "",
        s3_access_key_id_out=aws_out.get("access_key_id", "") or "",
        s3_secret_access_key_out=aws_out.get("secret_access_key", "") or "",
        s3_endpoint_out=ws.get("s3_endpoint_out", "") or "",
        max_retries=int(ws.get("export_max_retries", 3) or 3),
    )


def run_export(
    pdf_bytes: bytes, document, config: ExportConfig, transaction_fields=None
) -> ExportResult:
    """Exporte un document selon way_out (WS ou S3). Construit le payload et délègue.

    Ne gère pas le statut du document (c'est la tâche worker) ; renvoie un
    ExportResult indiquant succès/échec.
    """
    if not config.way_out:
        return ExportResult(ok=False, error="no_way_out")

    json_payload = build_payload(document, transaction_fields)
    try:
        if config.way_out == "WS":
            export_via_ws(pdf_bytes, json_payload, config, document.id)
        elif config.way_out == "S3":
            export_via_s3(pdf_bytes, json_payload, config, document.id)
        else:
            return ExportResult(
                ok=False, error="unknown_way_out", detail={"way_out": config.way_out}
            )
    except Exception as ex:  # noqa: BLE001
        logger.error("Export %s échoué pour %s : %s", config.way_out, document.id, ex)
        return ExportResult(ok=False, error=str(ex), detail={"way_out": config.way_out})

    return ExportResult(ok=True, detail={"way_out": config.way_out})


def export_via_ws(pdf_bytes: bytes, json_payload: str, config: ExportConfig, doc_id: str) -> None:
    """POST multipart : PDF (binaire) + JSON des index, vers l'URL du web service.

    Le plus robuste : binaire natif, pas de base64. Auth Bearer ou Basic selon
    la config. Lève en cas d'échec (la tâche gère le statut).
    """
    import requests

    from alambic_core.security.url_guard import UrlGuardError, validate_url

    if not config.ws_url:
        raise ValueError("URL du web service d'export manquante (ws_url)")

    # Garde-fou anti-SSRF : l'URL doit appartenir aux domaines autorisés du compte
    # (account.enrich_allowed_domains) et ne pas pointer vers une cible interne.
    try:
        validate_url(config.ws_url, allowed_domains=config.ws_allowed_domains)
    except UrlGuardError as e:
        # Erreur non transitoire : on ne réessaie pas, on refuse l'export.
        raise RuntimeError(f"URL d'export refusée : {e}") from e

    headers = {}
    auth = None
    if config.ws_auth_type == "bearer" and config.ws_token:
        headers["Authorization"] = f"Bearer {config.ws_token}"
    elif config.ws_auth_type == "basic" and ":" in config.ws_token:
        user, _, pwd = config.ws_token.partition(":")
        auth = (user, pwd)

    def _post():
        files = {
            "document": (f"{doc_id}.pdf", pdf_bytes, "application/pdf"),
            "metadata": (f"{doc_id}.json", json_payload.encode("utf-8"), "application/json"),
        }
        resp = requests.post(
            config.ws_url,
            files=files,
            headers=headers,
            auth=auth,
            timeout=config.ws_timeout,
        )
        if resp.status_code >= 500:
            # 5xx = côté serveur, transitoire → retry.
            raise ConnectionError(f"WS {resp.status_code} : {resp.text[:300]}")
        if resp.status_code >= 400:
            # 4xx = requête refusée, non transitoire → échec direct.
            raise RuntimeError(f"WS {resp.status_code} : {resp.text[:300]}")
        return resp

    _with_retry(_post, config.max_retries, "WS")


def export_via_s3(pdf_bytes: bytes, json_payload: str, config: ExportConfig, doc_id: str) -> None:
    """Upload vers un bucket S3 *tiers* du client (credentials sortants).

    Le PDF et le JSON côte à côte sous le préfixe configuré. Idempotent : si les
    deux objets existent déjà, on ne réexporte pas.
    """
    import boto3
    import botocore.exceptions

    if not config.s3_bucket_out:
        raise ValueError("Bucket S3 de sortie manquant (s3_bucket_out)")

    client_kwargs = {
        "aws_access_key_id": config.s3_access_key_id_out,
        "aws_secret_access_key": config.s3_secret_access_key_out,
        "region_name": config.s3_region_out or None,
    }
    if config.s3_endpoint_out:
        client_kwargs["endpoint_url"] = config.s3_endpoint_out
    s3 = boto3.client("s3", **client_kwargs)

    prefix = (config.s3_prefix_out or "").rstrip("/")
    base = f"{prefix}/{doc_id}" if prefix else doc_id
    pdf_key = f"{base}.pdf"
    json_key = f"{base}.json"

    # Idempotence : si les deux objets existent déjà, ne rien refaire.
    def _exists(key: str) -> bool:
        try:
            s3.head_object(Bucket=config.s3_bucket_out, Key=key)
            return True
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise  # AccessDenied ou autre → propager

    if _exists(pdf_key) and _exists(json_key):
        logger.info("Export S3 : %s déjà présent, idempotence", doc_id)
        return

    def _upload():
        s3.put_object(Bucket=config.s3_bucket_out, Key=json_key, Body=json_payload.encode("utf-8"))
        s3.put_object(Bucket=config.s3_bucket_out, Key=pdf_key, Body=pdf_bytes)

    _with_retry(_upload, config.max_retries, "S3")
