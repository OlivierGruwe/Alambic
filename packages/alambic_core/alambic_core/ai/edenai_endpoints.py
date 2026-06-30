"""alambic_core.ai.edenai_endpoints — construction des URLs EdenAI par région.

Les endpoints EdenAI suivent le motif :
    https://api.{region_prefix}edenai.run/v3/{feature_path}

où region_prefix vaut "eu." pour la région UE (souveraineté) et "" pour le global.
Le chemin dépend de la feature :
  - OCR            → v3/universal-ai
  - classification → v3/llm
  - extraction     → v3/llm
  - embedding      → v3/embeddings
  - catalogue providers → v3/info/{feature}/{subtype}
  - catalogue modèles   → v3/{llm|embeddings}/models

Construire les URLs à partir de la région (au lieu de les saisir à la main dans
la config) évite les erreurs d'URL — c'était la cause d'un 404 quand la config
pointait encore vers l'ancien endpoint v2 des embeddings.
"""

from __future__ import annotations

# Régions EdenAI supportées : code → préfixe de sous-domaine.
# "eu" insère "eu." (api.eu.edenai.run) ; "global" n'insère rien (api.edenai.run).
REGION_PREFIXES = {
    "eu": "eu.",
    "global": "",
}
DEFAULT_REGION = "eu"

# Chemin v3 par feature.
# Le LLM chat (classification, extraction) utilise le format OpenAI complet
# /v3/llm/chat/completions ; /v3/llm seul renvoie 404 sur l'appel chat.
FEATURE_PATHS = {
    "ocr": "universal-ai",
    "classifier": "llm/chat/completions",
    "extract": "llm/chat/completions",
    "embedding": "embeddings",
    # Détection vision (multi-document) : LLM multimodal (Pixtral/Mistral vision).
    # Endpoint V3 OpenAI-compatible « /v3/chat/completions » (sans /llm/), qui
    # accepte les messages multimodaux (image_url). Voir docs.edenai.co/v3.
    "vision": "chat/completions",
}


def _base(region: str) -> str:
    """Base d'URL pour une région (api.[eu.]edenai.run)."""
    code = (region or DEFAULT_REGION).lower()
    prefix = REGION_PREFIXES.get(code, REGION_PREFIXES[DEFAULT_REGION])
    return f"https://api.{prefix}edenai.run"


def endpoint_for(feature: str, region: str = DEFAULT_REGION) -> str:
    """URL d'appel d'une feature (ocr/classifier/extract/embedding) pour une région."""
    path = FEATURE_PATHS.get(feature, "llm")
    return f"{_base(region)}/v3/{path}"


def models_endpoint(kind: str, region: str = DEFAULT_REGION) -> str:
    """URL du catalogue de modèles. kind = 'llm' ou 'embeddings'."""
    sub = "embeddings" if kind == "embeddings" else "llm"
    return f"{_base(region)}/v3/{sub}/models"


def providers_endpoint(feature: str, subtype: str, region: str = DEFAULT_REGION) -> str:
    """URL du catalogue de providers (v3/info/{feature}/{subtype})."""
    return f"{_base(region)}/v3/info/{feature}/{subtype}"


def upload_endpoint(region: str = DEFAULT_REGION) -> str:
    """URL d'upload de fichier (v3/upload), construite selon la région.

    EdenAI référence ensuite le fichier par file_id ; l'upload doit viser la même
    région que l'appel OCR pour rester cohérent (pas d'upload global + OCR EU).
    """
    return f"{_base(region)}/v3/upload"


def normalize_llm_endpoint(url: str) -> str:
    """Répare un endpoint LLM stocké incomplet.

    Les anciennes configs pouvaient stocker `.../v3/llm` (sans /chat/completions),
    ce qui renvoie 404 sur l'appel chat. On complète ces URLs. Les endpoints déjà
    corrects (ou pointant ailleurs) sont laissés tels quels.
    """
    if not url:
        return url
    trimmed = url.rstrip("/")
    if trimmed.endswith("/v3/llm"):
        return trimmed + "/chat/completions"
    return url


def available_regions() -> list[str]:
    """Codes de région proposés dans l'UI."""
    return sorted(REGION_PREFIXES.keys())
