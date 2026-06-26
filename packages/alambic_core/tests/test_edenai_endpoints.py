"""Tests du constructeur d'endpoints EdenAI par région."""

from __future__ import annotations

from alambic_core.ai.edenai_endpoints import (
    available_regions,
    endpoint_for,
    models_endpoint,
    providers_endpoint,
)


def test_endpoint_eu_inserts_prefix():
    assert endpoint_for("ocr", "eu") == "https://api.eu.edenai.run/v3/universal-ai"
    assert endpoint_for("classifier", "eu") == "https://api.eu.edenai.run/v3/llm/chat/completions"
    assert endpoint_for("extract", "eu") == "https://api.eu.edenai.run/v3/llm/chat/completions"
    assert endpoint_for("embedding", "eu") == "https://api.eu.edenai.run/v3/embeddings"


def test_endpoint_global_no_prefix():
    assert endpoint_for("ocr", "global") == "https://api.edenai.run/v3/universal-ai"
    assert endpoint_for("embedding", "global") == "https://api.edenai.run/v3/embeddings"


def test_embedding_endpoint_is_v3_not_v2():
    # Régression : l'ancien défaut pointait vers /v2/text/embeddings (404).
    url = endpoint_for("embedding", "eu")
    assert "/v3/embeddings" in url
    assert "/v2/" not in url


def test_unknown_region_falls_back_to_eu():
    assert endpoint_for("ocr", "xyz") == "https://api.eu.edenai.run/v3/universal-ai"
    assert endpoint_for("ocr", "") == "https://api.eu.edenai.run/v3/universal-ai"


def test_models_endpoint():
    assert models_endpoint("embeddings", "eu") == "https://api.eu.edenai.run/v3/embeddings/models"
    assert models_endpoint("llm", "global") == "https://api.edenai.run/v3/llm/models"


def test_providers_endpoint():
    assert providers_endpoint("ocr", "ocr", "eu") == "https://api.eu.edenai.run/v3/info/ocr/ocr"


def test_available_regions():
    regions = available_regions()
    assert "eu" in regions
    assert "global" in regions


def test_upload_endpoint_follows_region():
    from alambic_core.ai.edenai_endpoints import upload_endpoint

    assert upload_endpoint("eu") == "https://api.eu.edenai.run/v3/upload"
    assert upload_endpoint("global") == "https://api.edenai.run/v3/upload"
    assert upload_endpoint("") == "https://api.eu.edenai.run/v3/upload"  # défaut EU


def test_llm_endpoints_use_chat_completions():
    """Régression : l'appel chat doit viser /chat/completions (pas /v3/llm seul → 404)."""
    from alambic_core.ai.edenai_endpoints import endpoint_for

    assert endpoint_for("classifier", "eu").endswith("/v3/llm/chat/completions")
    assert endpoint_for("extract", "eu").endswith("/v3/llm/chat/completions")


def test_normalize_llm_endpoint_repairs_old_value():
    from alambic_core.ai.edenai_endpoints import normalize_llm_endpoint

    assert (
        normalize_llm_endpoint("https://api.eu.edenai.run/v3/llm")
        == "https://api.eu.edenai.run/v3/llm/chat/completions"
    )
    assert (
        normalize_llm_endpoint("https://api.eu.edenai.run/v3/llm/")
        == "https://api.eu.edenai.run/v3/llm/chat/completions"
    )
    # Déjà correct → inchangé.
    already = "https://api.eu.edenai.run/v3/llm/chat/completions"
    assert normalize_llm_endpoint(already) == already
    assert normalize_llm_endpoint("") == ""
