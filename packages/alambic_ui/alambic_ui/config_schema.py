"""
alambic_ui.config_schema — cartographie des configurations.

Une Config a une structure mixte dans alambic_core :
  - champs simples (config_name, account_id, need_validation,
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
    "classifier_let_it_guess",
    "doctype_ids",
    "expected_doctype_ids",
    "filter_extensions",
    "fixed_page",
    "pdf_max_pages",
]

# ── Onglets IA + RECONNAISSANCE : bloc JSONB "edenai_settings" ────────────────
EDENAI_KEYS = [
    # Région EdenAI (pivot : construit les endpoints et filtre les modèles)
    "region",
    # OCR
    "ocr_engine",
    "ocr_preprocess",
    "ocr_rotation",
    "ocr_max_pixels",
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
    # OCR : prétraitement image (single par défaut) + rotation auto activée.
    "ocr_preprocess": "single",
    "ocr_rotation": True,
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
    "classifier_let_it_guess",
    "ocr_treat_images",
    "ocr_rotation",
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
    for k in SIMPLE_BOOL:
        data[k] = bool(getattr(config, k, False))

    # Doctypes attendus (complétude) : sérialisés en JSON pour le JS du formulaire.
    data["expected_doctypes"] = json.dumps(
        getattr(config, "expected_doctypes", None) or [], ensure_ascii=False
    )
    data["config_fields"] = json.dumps(
        getattr(config, "config_fields", None) or [], ensure_ascii=False
    )
    data["consolidation_ws"] = json.dumps(
        getattr(config, "consolidation_ws", None) or [], ensure_ascii=False
    )

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
    # Champs simples — le nom est normalisé en snake_case (sans accent/espaces).
    from alambic_core.domain.naming import to_snake_case

    config.config_name = to_snake_case(form_data.get("config_name") or "")
    for k in SIMPLE_BOOL:
        setattr(config, k, _checkbox(form_data, k))

    # Doctypes attendus (complétude) : JSON [{doctype_id, required}] depuis le
    # champ caché alimenté par le JS. Tolère vide/malformé → liste vide.
    config.expected_doctypes = _parse_expected_doctypes(form_data.get("expected_doctypes"))
    config.config_fields = _parse_config_fields(form_data.get("config_fields"))
    config.consolidation_ws = _parse_consolidation_ws(form_data.get("consolidation_ws"))

    # Blocs JSONB : on ne réécrit QUE les champs réellement présents dans le
    # formulaire soumis. Un champ absent de form_data (select désactivé, options
    # injectées en JS non encore chargées, onglet non rendu…) conserve sa valeur
    # existante — sinon une simple sauvegarde effacerait la config EdenAI (région,
    # providers, modèles, endpoints) alors que l'utilisateur n'y a pas touché.
    config.general = _merge_block(config.general, form_data, GENERAL_KEYS)
    config.edenai_settings = _merge_block(config.edenai_settings, form_data, EDENAI_KEYS)
    config.ws = _merge_block(config.ws, form_data, IN_KEYS + OUT_KEYS + EXPORT_KEYS)

    # Secrets : un dict chiffré par colonne ; vide saisi = inchangé.
    for col, mapping in SECRET_BLOCKS.items():
        existing = _load_secret(getattr(config, col, ""))
        changed = False
        for form_key, secret_key in mapping.items():
            val = form_data.get(form_key)
            # Filet de sécurité : un navigateur peut soumettre la valeur de
            # remplacement « •••• » (placeholder autofill). Ne JAMAIS l'enregistrer
            # comme clé — sinon on écrase le vrai secret par des puces.
            if val and not _is_placeholder_secret(val):
                existing[secret_key] = val
                changed = True
        if changed:
            setattr(config, col, json.dumps(existing, ensure_ascii=False))


def _parse_expected_doctypes(raw) -> list:
    """Parse le champ expected_doctypes du formulaire (JSON) en liste normalisée.

    Format attendu : [{"doctype_id": "...", "required": true}, ...].
    Tolère vide, JSON malformé, ou format partiel → renvoie une liste propre.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    result = []
    seen = set()
    for item in data:
        if isinstance(item, dict) and item.get("doctype_id"):
            did = str(item["doctype_id"])
            if did in seen:
                continue
            seen.add(did)
            result.append({"doctype_id": did, "required": bool(item.get("required", True))})
    return result


def _parse_config_fields(raw) -> list:
    """Parse le champ config_fields du formulaire (JSON) en liste normalisée.

    Format : [{"field_name", "field_label", "source_type", "source_key",
    "default_value"}]. Les champs sans field_name sont ignorés. source_type est
    contraint à 'context' ou 'computed'.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("field_name") or "").strip()
        if not name:
            continue
        source_type = str(item.get("source_type") or "").strip().lower()
        if source_type not in ("context", "computed"):
            source_type = "context"
        result.append(
            {
                "field_name": name,
                "field_label": str(item.get("field_label") or "").strip(),
                "source_type": source_type,
                "source_key": str(item.get("source_key") or "").strip(),
                "default_value": str(item.get("default_value") or ""),
            }
        )
    return result


def _parse_consolidation_ws(raw) -> list:
    """Parse le champ consolidation_ws du formulaire (JSON) en liste normalisée.

    Format : [{"name", "url", "method", "response_status_path",
    "response_mapping", "on_failure"}]. Les définitions sans name sont ignorées.
    method ∈ {GET, POST}, on_failure ∈ {skip, error}, response_mapping en dict.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    result = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        method = str(item.get("method") or "GET").strip().upper()
        if method not in ("GET", "POST"):
            method = "GET"
        on_failure = str(item.get("on_failure") or "skip").strip().lower()
        if on_failure not in ("skip", "error"):
            on_failure = "skip"
        mapping = item.get("response_mapping")
        if not isinstance(mapping, dict):
            mapping = {}
        result.append(
            {
                "name": name,
                "target_field": str(item.get("target_field") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "method": method,
                "response_status_path": str(item.get("response_status_path") or "").strip(),
                "response_mapping": {str(k): str(v) for k, v in mapping.items()},
                "on_failure": on_failure,
            }
        )
    return result


def _load_secret(raw: str) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


# Caractères de puce/masquage qu'un navigateur peut soumettre à la place d'un
# secret (placeholder autofill). Une valeur composée UNIQUEMENT de ces caractères
# n'est jamais un vrai secret et ne doit pas écraser celui en base.
_SECRET_PLACEHOLDER_CHARS = set("•·*●∙*◦⦿\u2022\u00b7\u25cf\u2219")


def _is_placeholder_secret(val: str) -> bool:
    """True si la valeur n'est qu'une suite de puces/masques (autofill placeholder)."""
    s = (val or "").strip()
    return bool(s) and all(c in _SECRET_PLACEHOLDER_CHARS for c in s)


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


def _merge_block(existing: dict | None, form_data, keys) -> dict:
    """Fusionne un bloc JSONB avec le formulaire, en préservant l'existant.

    Règle, par clé :
      - booléen (BOOL_KEYS) : lu comme une case à cocher (absent = décoché). Une
        case décochée n'apparaît pas dans form_data, donc l'absence signifie False
        — c'est voulu pour les booléens.
      - autre : si la clé est PRÉSENTE dans form_data, on prend la valeur soumise
        (même vide = effacement volontaire). Si la clé est ABSENTE, on CONSERVE la
        valeur existante.

    Évite qu'une sauvegarde efface des réglages (région, providers, modèles,
    endpoints EdenAI) que le formulaire n'a pas renvoyés — par ex. un select dont
    les options sont injectées en JS, ou un champ désactivé non soumis.
    """
    out = dict(existing or {})
    for k in keys:
        if k in BOOL_KEYS:
            out[k] = _from_form(k, _checkbox(form_data, k))
        elif k in form_data:
            out[k] = _from_form(k, form_data.get(k))
        # sinon : clé absente du formulaire → on garde la valeur existante.
    return out
