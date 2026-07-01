"""
Déclencheur d'ingestion — portage de start_workflow.py (ex-IngestionTrigger).

Migration souveraine : déclenchement en DÉPÔT DIRECT (cf. décision d'archi).
Sur AWS, un événement S3 → EventBridge → Lambda démarrait la Step Function. Ici,
l'API/UI qui reçoit l'upload appelle directement start_ingestion() : elle pousse
le fichier dans Garage (double upload work + backup) puis lance run_ingestion via
Celery. Pas d'événement de stockage à attendre, pas de composant intermédiaire.

Idempotence (décision retenue) : par transaction_key en base. La clé est le hash
déterministe (bucket + clé d'origine), identique à l'ancien transaction_ref. Si
une transaction existe déjà pour cette clé, on ne relance pas le workflow — ça
remplace l'idempotence par fenêtre glissante de Step Functions, en plus robuste
(la vérité est en base, pas dans une fenêtre temporelle).

Correspondance flowerscan_lib → alambic :
    parse_key                       → parse_upload_key (inchangé)
    create_s3_obj (work + backup)   → storage.put_object x2 (parallèle)
    compute_transaction_key         → identique (sha256 bucket:key)
    config validation               → ConfigRepository
    start_execution (Step Functions)→ run_ingestion.delay(payload)
    create_rejected                 → Transaction status=REJECTED
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from alambic_core.db.session import session_scope
from alambic_core.models import Transaction
from alambic_core.repositories import ConfigRepository, TransactionRepository

from alambic_workers import storage
from alambic_workers.celery_app import app

DEFAULT_ORIGIN = "UNKNOWN"
ORIGIN_PREFIXES = {
    "UI_IMPORT": "UI_IMPORT",
    "FTP": "FTP",
    "S3": "S3",
    "API": "API",
    # Canaux d'ingestion : sans ces entrées, les dépôts mail et web service
    # retombaient sur UNKNOWN au parsing (origine perdue dans les statistiques).
    "MAIL": "MAIL",
    "WS": "WS",
}


class InvalidInputError(ValueError):
    """Entrée invalide (clé malformée, config inconnue…)."""


def compute_transaction_key(bucket: str, key: str) -> str:
    """Clé déterministe d'une transaction (hash bucket+clé). Identique à l'original."""
    return hashlib.sha256(f"{bucket}:{key}".encode()).hexdigest()


def parse_upload_key(key: str) -> tuple[str, str, str, str]:
    """__uploads__/<accountId>/<configId>/<origin>/<filename>.

    Reproduit parse_key de l'original, y compris la résolution de l'origine.
    """
    parts = key.split("/")
    if len(parts) != 5:
        raise InvalidInputError(f"Structure de clé invalide : {key}")
    _, account_id, config_id, origin_raw, filename = parts
    origin = ORIGIN_PREFIXES.get(origin_raw, DEFAULT_ORIGIN)
    return account_id, config_id, origin, filename


def _get_extension(filename: str) -> str:
    _, _, ext = filename.rpartition(".")
    return ext if ext != filename else ""


def _extension_allowed(config, filename: str) -> bool:
    """Vrai si l'extension du fichier est autorisée par la config.

    La config peut restreindre les types acceptés via general.filter_extensions
    (liste séparée par virgules/espaces/points-virgules, ex. « pdf, jpg, png »).
    Liste vide ou absente → toutes les extensions sont autorisées.
    La comparaison est insensible à la casse et tolère un point en préfixe.
    """
    raw = (config.general or {}).get("filter_extensions", "") if config is not None else ""
    raw = (raw or "").strip()
    if not raw:
        return True  # pas de restriction
    allowed = {
        part.strip().lstrip(".").lower()
        for part in raw.replace(",", " ").replace(";", " ").split()
        if part.strip()
    }
    if not allowed:
        return True
    ext = _get_extension(filename).lower()
    return ext in allowed


def start_ingestion(
    *,
    bucket: str,
    object_key: str,
    local_path: str,
    original_filename: str = "",
    author: str = "",
    metadata: dict | None = None,
) -> dict | None:
    """Point d'entrée du dépôt direct.

    bucket / object_key : emplacement du fichier d'origine (zone __uploads__).
    local_path          : chemin local du fichier à pousser dans Garage.
    original_filename / author : métadonnées (sinon dérivées de la clé).

    Renvoie {"transactionId": ...} si le workflow est lancé, None si c'est un
    doublon (idempotence) ou si la transaction a été rejetée.
    """
    origin = DEFAULT_ORIGIN
    filename = ""
    transaction_key = compute_transaction_key(bucket, object_key)

    try:
        account_id, config_id, origin, filename = parse_upload_key(object_key)
        original_filename = original_filename or filename

        # ── Validation de la config + idempotence (une seule session) ────────
        with session_scope() as s:
            config = ConfigRepository(s).get(config_id)
            if config is None:
                raise InvalidInputError(f"configId invalide ({config_id})")
            if config.account_id != account_id:
                raise InvalidInputError(
                    f"accountId ({account_id}) et configId ({config_id}) incohérents"
                )
            # Config inactive : on n'accepte plus de nouvelles transactions.
            if not getattr(config, "is_active", True):
                raise InvalidInputError(f"config inactive ({config_id})")
            # Filtrage des extensions : si la config restreint les types de fichiers
            # acceptés (general.filter_extensions), on refuse les autres.
            if not _extension_allowed(config, filename):
                allowed = (config.general or {}).get("filter_extensions", "")
                raise InvalidInputError(
                    f"extension non autorisée pour {filename} (autorisées : {allowed})"
                )

            # Idempotence : transaction déjà créée pour cette clé → on skip.
            existing = TransactionRepository(s).by_transaction_key(transaction_key)
            if existing is not None:
                return None

            # Champs propagés : capturés dans la session (config détachée ensuite).
            _config_fields = list(getattr(config, "config_fields", None) or [])
            _config_name = config.config_name or ""

        # ── Identifiants + destinations ──────────────────────────────────────
        oid = uuid4().hex[:16]
        transaction_id = f"trx-{oid}"
        document_id = f"doc-{oid}"
        ext = _get_extension(filename)

        work_bucket = os.environ.get("ALAMBIC_S3_WORK_BUCKET", "alambic-work")
        input_bucket = os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input")
        work_key = "/".join(
            ["__transactions__", account_id, config_id, transaction_id, f"{transaction_id}.{ext}"]
        )
        backup_key = object_key.replace("__uploads__", "__backup__")

        # ── Double upload parallèle (work + backup), comme l'original ────────
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_work = pool.submit(storage.put_object, work_bucket, work_key, local_path, metadata)
            fut_backup = pool.submit(
                storage.put_object, input_bucket, backup_key, local_path, metadata
            )
            fut_work.result()
            fut_backup.result()

        # ── Champs propagés : résolution + ajout aux index metadata ──────────
        # Contexte = métadonnées source (email from/subject…) + tokens d'exécution.
        propagated = []
        if _config_fields:
            from alambic_core.services.config_fields import resolve_config_fields

            ctx = dict(metadata or {})
            ctx.setdefault("transaction_id", transaction_id)
            ctx.setdefault("config_name", _config_name)
            propagated = [
                {"name": r["name"], "value": r["value"]}
                for r in resolve_config_fields(_config_fields, ctx)
            ]

        # ── Payload du workflow (format attendu par run_ingestion) ───────────
        payload = {
            "cid": oid,
            "accountId": account_id,
            "configId": config_id,
            "process": "STARTED",
            "transaction": {
                "transactionId": transaction_id,
                "transaction_key": transaction_key,
            },
            "datas": [
                {"name": "origin", "value": origin},
                {"name": "original_filename", "value": original_filename},
                {"name": "author", "value": author},
                # Champs propagés résolus → index metadata du document initial.
                # _write_metadata_indexes les pose, puis _copy_parent_indexes les
                # recopie vers les sous-documents issus d'un découpage.
                *propagated,
            ],
            "documents": [
                {
                    "documentId": document_id,
                    "file": {"bucket": work_bucket, "key": work_key},
                    "backup": {"bck_bucket": input_bucket, "bck_key": backup_key},
                }
            ],
        }

        # Politique de contenu mail (content_mode + filtre PJ) : transmise à
        # l'extraction (EmlProcessor) quand le dépôt provient d'une boîte mail.
        if metadata and metadata.get("mail_policy"):
            payload["mail_policy"] = metadata["mail_policy"]

        # ── Démarrage du workflow Celery (remplace start_execution) ──────────
        run = app.signature("alambic_workers.ingestion.run", args=[payload])
        run.delay()
        return {"transactionId": transaction_id}

    except Exception:
        # create_rejected : trace une transaction rejetée en base.
        # On loggue l'exception (un except muet masque les vraies causes).
        import logging

        logging.getLogger(__name__).exception(
            "start_ingestion a échoué pour %s/%s", bucket, object_key
        )
        try:
            with session_scope() as s:
                s.add(
                    Transaction(
                        id=f"trx-rejected-{transaction_key[:12]}",
                        transaction_key=transaction_key,
                        status="REJECTED",
                        origin=origin,
                    )
                )
        except Exception:
            logging.getLogger(__name__).exception("create_rejected a aussi échoué")
        return None
