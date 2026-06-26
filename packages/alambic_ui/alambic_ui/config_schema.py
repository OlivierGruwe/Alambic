"""
alambic_ui.config_schema — cartographie des configurations.

Une Config a une structure mixte dans alambic_core :
  - champs simples (config_name, account_id, doctype_id, need_validation,
    multi_doc_detect) ;
  - 3 blocs JSONB : general, edenai_settings, ws ;
  - 6 secrets chiffrés : ftp_in_enc, ftp_out_enc, aws_in_enc, aws_out_enc,
    flower_enc (→ web service d'export), edenai_secret_enc.

L'UI présente tout ça en 6 onglets (repris de l'ancienne webapp) : Général, IA,
Reconnaissance, Entrées, Sorties, Export (WS). Ce module fait le pont entre les
champs de formulaire (plats) et cette structure (blocs + secrets).

Les secrets sont stockés en JSON chiffré (un bloc par "côté") : un dict
sérialisé dans la colonne *_enc correspondante.
"""

from __future__ import annotations

import json

# ── Champs simples (colonnes directes du modèle) ─────────────────────────────
SIMPLE_BOOL = ["need_validation", "multi_doc_detect"]

# ── Onglet GÉNÉRAL : bloc JSONB "general" ────────────────────────────────────
GENERAL_KEYS = [
    "auto_validation_threshold",
    "completeness_check",
    "doctype_ids",
    "expected_doctype_ids",
    "filter_extensions",
    "fixed_page",
    "multi_doc_max_workers",
    "pdf_max_pages",
]

# ── Onglets IA + RECONNAISSANCE : bloc JSONB "edenai_settings" ────────────────
EDENAI_KEYS = [
    # Région EdenAI (pivot : construit les endpoints et filtre les modèles)
    "region",
    # OCR
    "ocr_end_point",
    "ocr_provider",
    "ocr_language",
    "ocr_treat_images",
    "fallback_ocr_provider",
    # Classifier
    "classifier_end_point",
    "classifier_provider",
    "classifier_model",
    "classifier_confidence_level",
    "classifier_let_it_guess",
    "classifier_max_chars",
    "classifier_max_pages",
    "fallback_classifier_provider",
    "fallback_classifier_model",
    # Extraction
    "extract_end_point",
    "extract_provider",
    "extract_model",
    "fallback_extract_provider",
    "fallback_extract_model",
    # Reconnaissance (vision / object detection)
    "object_detection_provider",
    "fallback_object_detection_provider",
    "vision_llm_provider",
    "vision_llm_model",
    "fallback_vision_llm_provider",
    "fallback_vision_llm_model",
]

# ── Onglets ENTRÉES / SORTIES : bloc JSONB "ws" (non-secrets) ─────────────────
IN_KEYS = [
    "way_in",
    "s3_bucket_in",
    "s3_prefix_in",
    "aws_region_in",
    "ftp_server_in",
    "ftp_port_in",
    "ftp_user_in",
    "ftp_remote_dir_in",
]
OUT_KEYS = [
    "way_out",
    "s3_bucket_out",
    "s3_prefix_out",
    "aws_region_out",
    "ftp_server_out",
    "ftp_port_out",
    "ftp_user_out",
    "ftp_remote_dir_out",
]

# ── Onglet EXPORT (WS générique, remplace Flower) : bloc "ws" ─────────────────
# Robustesse + URL + type d'auth (les credentials d'auth sont des secrets).
EXPORT_KEYS = [
    "export_url",
    "export_auth_type",  # auth_type: none|bearer|basic|api_key
    "export_api_key_header",  # nom du header pour api_key
    "export_timeout",
    "export_max_retries",
    "export_retry_backoff",
    "export_verify_ssl",
]
EXPORT_AUTH_TYPES = ["none", "bearer", "basic", "api_key"]

# ── Listes de référence pour autocomplétion (datalists) ──────────────────────
# Valeurs courantes, à titre de suggestion (saisie libre conservée). À terme,
# elles seront peuplées dynamiquement depuis EdenAI (chantier pipeline IA).
WAY_CHOICES = ["S3", "FTP"]
WAY_IN_CHOICES = ["S3", "FTP"]
WAY_OUT_CHOICES = ["S3", "FTP", "WS"]  # WS = export web service
LLM_PROVIDERS = ["mistral", "anthropic", "openai"]
OCR_PROVIDERS = ["ocr/ocr/mistral", "ocr/ocr/amazon"]
LLM_MODELS = [
    "mistral/mistral-small-latest",
    "mistral/mistral-large-latest",
    "mistral/magistral-small-latest",
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-opus-4-5-20251101",
    "anthropic/claude-sonnet-4-5-20250929",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
]
EMBEDDING_PROVIDERS: list[str] = []  # embedding local (TEI), plus de provider tiers
EMBEDDING_MODELS: list[str] = []
OBJECT_DETECTION_PROVIDERS = ["amazon", "api4ai"]

# Langues OCR proposées (code, libellé).
OCR_LANGUAGES = [
    ("", "— Auto —"),
    ("fr", "Français"),
    ("en", "Anglais"),
    ("de", "Allemand"),
]

# Valeurs par défaut à la création d'une config.
DEFAULTS = {
    "auto_validation_threshold": "0.9",
    "classifier_confidence_level": "0.95",
    "export_timeout": "30",
    "export_max_retries": "3",
    "export_verify_ssl": True,
    "way_in": "S3",
    "way_out": "S3",
    # Région EdenAI : pivot qui construit les endpoints (api.eu. vs api.) et
    # filtre les modèles. Les anciens *_end_point ne sont plus saisis.
    "region": "eu",
}

# ── Secrets (chiffrés). Chaque "côté" = un dict JSON dans une colonne *_enc ───
# (clé de formulaire -> clé dans le dict secret)
SECRET_BLOCKS = {
    "ftp_in_enc": {"ftp_password_in": "password"},
    "ftp_out_enc": {"ftp_password_out": "password"},
    "aws_in_enc": {
        "aws_access_key_id_in": "access_key_id",
        "aws_secret_access_key_in": "secret_access_key",
    },
    "aws_out_enc": {
        "aws_access_key_id_out": "access_key_id",
        "aws_secret_access_key_out": "secret_access_key",
    },
    # Export WS (ex-Flower) : credentials selon le type d'auth.
    "flower_enc": {
        "export_auth_token": "token",  # bearer
        "export_auth_user": "user",  # basic
        "export_auth_password": "password",  # basic
        "export_api_key_value": "api_key",  # api_key
    },
    "edenai_secret_enc": {"edenai_secret_key": "secret_key"},
}

# Clés de formulaire qui sont des secrets (pour le masquage).
SECRET_FORM_KEYS = {fk for mapping in SECRET_BLOCKS.values() for fk in mapping}

# Clés booléennes (cases à cocher) au sein des blocs.
BOOL_KEYS = {
    "completeness_check",
    "classifier_let_it_guess",
    "ocr_treat_images",
    "export_verify_ssl",
}


def default_values() -> dict:
    """Valeurs par défaut pour une nouvelle config (formulaire de création)."""
    data: dict = {}
    for k in GENERAL_KEYS + EDENAI_KEYS + IN_KEYS + OUT_KEYS + EXPORT_KEYS:
        data[k] = DEFAULTS.get(k, False if k in BOOL_KEYS else "")
    return data


def parse_config(config) -> dict:
    """Modèle Config → dict de valeurs de formulaire (plat).

    Lit les champs simples, déplie les blocs JSONB. Les secrets ne sont JAMAIS
    remontés (champs masqués) ; on signale seulement leur présence via has_secret.
    """
    data: dict = {}
    # Champs simples
    data["config_name"] = config.config_name or ""
    data["account_id"] = config.account_id or ""
    data["doctype_id"] = config.doctype_id or ""
    for k in SIMPLE_BOOL:
        data[k] = bool(getattr(config, k, False))

    # Blocs JSONB
    general = config.general or {}
    edenai = config.edenai_settings or {}
    ws = config.ws or {}
    for k in GENERAL_KEYS:
        data[k] = _to_form(k, general.get(k))
    for k in EDENAI_KEYS:
        data[k] = _to_form(k, edenai.get(k))
    for k in IN_KEYS + OUT_KEYS + EXPORT_KEYS:
        data[k] = _to_form(k, ws.get(k))

    return data


def secret_presence(config) -> dict:
    """Indique quels blocs de secrets sont renseignés (pour l'affichage)."""
    presence = {}
    for col in SECRET_BLOCKS:
        raw = getattr(config, col, "") or ""
        presence[col] = bool(raw.strip())
    return presence


def apply_form_to_config(config, form_data) -> None:
    """Applique les valeurs du formulaire au modèle Config.

    Reconstruit les blocs JSONB et, pour les secrets, ne remplace que ceux qui
    ont été saisis (vide = inchangé).
    """
    # Champs simples
    config.config_name = (form_data.get("config_name") or "").strip()
    config.doctype_id = (form_data.get("doctype_id") or "").strip()
    for k in SIMPLE_BOOL:
        setattr(config, k, _checkbox(form_data, k))

    # Blocs JSONB
    config.general = {k: _from_form(k, form_data.get(k)) for k in GENERAL_KEYS}
    config.edenai_settings = {k: _from_form(k, form_data.get(k)) for k in EDENAI_KEYS}
    config.ws = {k: _from_form(k, form_data.get(k)) for k in IN_KEYS + OUT_KEYS + EXPORT_KEYS}

    # Secrets : un dict chiffré par colonne ; vide saisi = inchangé.
    for col, mapping in SECRET_BLOCKS.items():
        existing = _load_secret(getattr(config, col, ""))
        changed = False
        for form_key, secret_key in mapping.items():
            val = form_data.get(form_key)
            if val:  # saisi → on remplace
                existing[secret_key] = val
                changed = True
        if changed:
            setattr(config, col, json.dumps(existing, ensure_ascii=False))


def _load_secret(raw: str) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _checkbox(form_data, key) -> bool:
    return key in form_data


def _to_form(key, value):
    """Valeur de bloc JSONB → valeur de formulaire."""
    if key in BOOL_KEYS:
        return bool(value)
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _from_form(key, value):
    """Valeur de formulaire → valeur de bloc JSONB."""
    if key in BOOL_KEYS:
        return bool(value) if isinstance(value, bool) else (value is not None and value != "")
    return (value or "").strip()
