"""alambic_core.services.consolidation_client — appel d'un WS de consolidation.

Porté de FlowerScan (ia.fcl_consolidation_client). Appelle un WS externe avec la
valeur d'un champ extrait et retourne un résultat normalisé {statut, données}.

Patterns de robustesse :
  - garde anti-SSRF re-vérifiée AU MOMENT de l'appel (le DNS peut avoir changé
    depuis la sauvegarde) ;
  - requests.Session + Retry limité (le WS est synchrone, dans le chemin du
    pipeline : on ne veut pas accumuler les retries) ;
  - secret d'auth déchiffré à l'usage, jamais loggué ;
  - timeout strict ; on_failure (skip/error) appliqué par l'appelant.

Contrat de sortie (ConsolidationResult) :
  {
    "ok":     bool,                                # appel réussi (HTTP 2xx + parse)
    "status": "VALID"|"INVALID"|"UNKNOWN"|"ERROR",
    "data":   {index_name: value, ...},            # issu de response_mapping
    "error":  str | None,                          # message si ok is False
  }
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..db.types import get_secret_provider
from ..security.url_guard import UrlGuardError, validate_url
from .consolidation_ws import normalize_ws_definition

logger = logging.getLogger(__name__)


def _empty(v) -> bool:
    return v is None or str(v).strip() == ""


def _build_session(timeout_retries: int = 1) -> requests.Session:
    """Session avec retry limité (WS synchrone dans le chemin du pipeline)."""
    s = requests.Session()
    retry = Retry(
        total=timeout_retries,
        connect=timeout_retries,
        read=timeout_retries,
        status=timeout_retries,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _decrypt_secret(enc_value: str) -> str:
    """Déchiffre un secret via le secret provider. '' si vide/échec."""
    if _empty(enc_value):
        return ""
    try:
        return get_secret_provider().decrypt(enc_value) or ""
    except Exception:  # noqa: BLE001
        logger.warning("consolidation_client : déchiffrement du secret échoué")
        return ""


def _classify_status(ws: dict, body) -> str:
    """Détermine le statut de validation depuis la réponse.

    - response_status_path défini : lit cette clé (truthy → VALID, sinon INVALID).
    - sinon : VALID si la réponse est non vide, UNKNOWN sinon.
    """
    path = ws.get("response_status_path")
    if path and isinstance(body, dict):
        val = body.get(path)
        if isinstance(val, bool):
            return "VALID" if val else "INVALID"
        if _empty(val):
            return "INVALID"
        low = str(val).strip().lower()
        if low in ("true", "1", "ok", "found", "valid", "yes"):
            return "VALID"
        if low in ("false", "0", "not_found", "invalid", "no"):
            return "INVALID"
        return "VALID"  # valeur présente, non reconnue comme négative
    if body:
        return "VALID"
    return "UNKNOWN"


def _extract_data(ws: dict, body) -> dict:
    """Applique response_mapping : {clé_réponse: nom_index} → {nom_index: valeur}."""
    mapping = ws.get("response_mapping") or {}
    out = {}
    if not isinstance(body, dict) or not isinstance(mapping, dict):
        return out
    for resp_key, index_name in mapping.items():
        if _empty(index_name):
            continue
        val = body.get(resp_key)
        if val is not None:
            out[index_name] = str(val)
    return out


def call_consolidation_ws(ws_def: dict, value: str, allowed_domains=None) -> dict:
    """Appelle le WS `ws_def` avec `value`. Ne lève jamais (erreurs encapsulées)."""
    ws = normalize_ws_definition(ws_def)

    def _err(msg: str) -> dict:
        return {"ok": False, "status": "ERROR", "data": {}, "error": msg}

    raw_value = "" if value is None else str(value)

    # URL : substitution du placeholder {value} (encodé pour l'URL).
    url = (ws.get("url") or "").replace("{value}", quote(raw_value, safe=""))

    # Garde anti-SSRF AU MOMENT DE L'APPEL (re-résolution DNS).
    try:
        validate_url(url, allowed_domains=allowed_domains)
    except UrlGuardError as e:
        return _err(f"url refusée : {e}")

    method = ws["method"]
    timeout = ws["timeout"]
    headers = {"Accept": "application/json"}
    params = {}
    json_body = None

    # Auth
    auth_type = ws.get("auth_type", "none")
    if auth_type in ("header", "query"):
        secret = _decrypt_secret(ws.get("auth_secret", ""))
        key = ws.get("auth_key", "")
        if key and secret:
            if auth_type == "header":
                headers[key] = secret
            else:  # query
                params[key] = secret

    # Corps de requête pour POST
    if method == "POST":
        field = ws.get("request_field") or "value"
        json_body = {field: raw_value}

    session = _build_session()
    try:
        resp = session.request(
            method=method,
            url=url,
            params=params or None,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
    except requests.Timeout:
        return _err(f"timeout après {timeout}s")
    except requests.RequestException:
        # On ne loggue jamais l'URL complète (peut contenir le secret en query).
        logger.warning("consolidation_client : erreur réseau")
        return _err("erreur réseau")
    finally:
        session.close()

    if resp.status_code >= 400:
        return _err(f"HTTP {resp.status_code}")

    # Parse JSON (tolérant : sinon body = texte brut).
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        body = resp.text

    return {
        "ok": True,
        "status": _classify_status(ws, body),
        "data": _extract_data(ws, body),
        "error": None,
    }
