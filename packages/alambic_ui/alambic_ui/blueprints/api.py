"""alambic_ui.blueprints.api — endpoints WS (machine-to-machine) authentifiés par clé API.

Contrairement aux blueprints d'administration (session + login), ces routes sont
appelées par des web services : l'authentification se fait par clé API présentée
dans l'en-tête « Authorization: Bearer <clé> ». La clé détermine la portée :
- clé admin (is_admin) : accès à toutes les configurations / tous comptes ;
- clé normale : limitée à son account_id.

Routes :
- GET  /api/v1/configs  → configurations disponibles pour la clé.
- POST /api/v1/ingest   → dépôt d'un document (multipart) + config_id, lance
                          l'ingestion (même mécanisme que l'upload UI : dépôt
                          dans Garage, le worker prend le relais).
"""

from __future__ import annotations

import logging
from functools import wraps

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Config
from alambic_core.services.api_keys import verify_key
from alambic_core.storage import build_upload_key, input_bucket, put_bytes
from flask import Blueprint, g, jsonify, request
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _session():
    return get_sessionmaker()()


def _extract_bearer() -> str:
    """Récupère la clé depuis « Authorization: Bearer <clé> » (ou en-tête vide)."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def require_api_key(fn):
    """Décorateur : exige une clé API valide ; place l'ApiKey dans g.api_key."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        plaintext = _extract_bearer()
        if not plaintext:
            return jsonify({"error": "missing_api_key"}), 401
        with _session() as s:
            key = verify_key(s, plaintext)
            if key is None:
                return jsonify({"error": "invalid_or_expired_api_key"}), 401
            # On mémorise les attributs de portée (détachés de la session).
            g.api_key = {
                "id": key.id,
                "is_admin": bool(key.is_admin),
                "account_id": key.account_id,
                "name": key.apikey_name,
            }
        return fn(*args, **kwargs)

    return wrapper


def _visible_configs(session):
    """Configurations visibles selon la portée de la clé (admin = toutes)."""
    q = session.query(Config)
    if not g.api_key["is_admin"]:
        q = q.filter(Config.account_id == g.api_key["account_id"])
    return q.all()


@api_bp.route("/configs", methods=["GET"])
@require_api_key
def list_configs():
    """Liste les configurations disponibles pour la clé (id + nom + compte)."""
    with _session() as s:
        configs = _visible_configs(s)
        payload = [
            {
                "config_id": c.id,
                "config_name": c.config_name,
                "account_id": c.account_id,
            }
            for c in configs
        ]
    return jsonify({"configs": payload, "count": len(payload)})


@api_bp.route("/ingest", methods=["POST"])
@require_api_key
def ingest():
    """Dépose un document et lance l'ingestion.

    Corps multipart : `file` (le document) + `config_id` (cible). La portée de la
    clé est vérifiée : une clé non-admin ne peut ingérer que pour son compte.
    """
    config_id = (request.form.get("config_id") or "").strip()
    if not config_id:
        return jsonify({"error": "missing_config_id"}), 400

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "missing_file"}), 400

    with _session() as s:
        conf = s.get(Config, config_id)
        if conf is None:
            return jsonify({"error": "config_not_found"}), 404
        # Vérification de portée : clé non-admin limitée à son compte.
        if not g.api_key["is_admin"] and conf.account_id != g.api_key["account_id"]:
            return jsonify({"error": "config_not_allowed"}), 403
        account_id = conf.account_id or ""

    content = file.read()
    if not content:
        return jsonify({"error": "empty_file"}), 400

    filename = secure_filename(file.filename)
    # Origine « WS » pour tracer que le dépôt vient d'un web service.
    key = build_upload_key(account_id, config_id, filename, "WS")
    bucket = input_bucket()

    try:
        put_bytes(bucket, key, content)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WS ingest : dépôt Garage échoué pour %s", filename)
        return jsonify({"error": "storage_error", "detail": str(exc)}), 502

    logger.info(
        "WS ingest : %s déposé (config=%s, compte=%s, clé=%s)",
        filename, config_id, account_id, g.api_key["name"],
    )
    return jsonify(
        {
            "status": "accepted",
            "filename": filename,
            "config_id": config_id,
            "object_key": key,
        }
    ), 202
