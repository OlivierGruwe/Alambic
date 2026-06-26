"""Tests du catalogue EdenAI (listing providers/modèles, repli, parsing)."""

from __future__ import annotations

from conftest import login

from alambic_ui.edenai_catalog import (
    _parse_info,
    _parse_llm_by_provider,
    list_llms,
    list_providers,
)


def test_fallback_without_key():
    r = list_providers("ocr", "ocr")
    assert r["source"] == "fallback"
    assert len(r["providers"]) > 0
    r2 = list_llms()
    assert r2["source"] == "fallback"
    assert len(r2["providers"]) > 0
    assert isinstance(r2["by_provider"], dict)


def test_parse_info_list_format():
    payload = [
        {"provider": "mistral", "model": "mistral-large"},
        {"provider": "amazon"},
    ]
    provs, models = _parse_info(payload)
    assert "mistral" in provs and "amazon" in provs
    assert "mistral/mistral-large" in models


def test_parse_info_providers_key():
    payload = {"providers": [{"provider": "google"}, "microsoft"]}
    provs, _ = _parse_info(payload)
    assert "google" in provs and "microsoft" in provs


def test_parse_info_unexpected_format():
    provs, models = _parse_info({"weird": 1})
    assert provs == [] and models == []


def test_parse_llm_by_provider_filters():
    payload = {
        "data": [
            {
                "owned_by": "amazon",
                "model_name": "jamba",
                "capabilities": {"supports_response_schema": False},
                "regions": [{"code": "eu"}],
            },
            {
                "owned_by": "mistral",
                "model_name": "mistral-large-latest",
                "capabilities": {"supports_response_schema": True},
                "regions": [{"code": "eu"}],
            },
            {
                "owned_by": "openai",
                "model_name": "gpt-4o",
                "capabilities": {"supports_response_schema": True},
                "regions": [{"code": "us"}],
            },
        ]
    }
    bp, regions = _parse_llm_by_provider(payload)
    # mistral gardé (schema true), avec ses régions
    assert "mistral" in bp
    assert bp["mistral"][0]["name"] == "mistral-large-latest"
    assert "eu" in bp["mistral"][0]["regions"]
    assert "amazon" not in bp  # schema False
    # openai présent (filtrage région délégué au front), région us notée
    assert "openai" in bp
    assert regions == {"eu", "us"}


def test_parse_llm_model_name_clean():
    payload = {
        "data": [
            {
                "id": "amazon/amazon.nova-2-lite-v1:0@eu",
                "owned_by": "amazon",
                "model_name": "amazon.nova-2-lite-v1:0",
                "capabilities": {"supports_response_schema": True},
                "regions": [{"code": "eu"}],
            },
        ]
    }
    bp, _ = _parse_llm_by_provider(payload)
    assert bp["amazon"][0]["name"] == "amazon.nova-2-lite-v1:0"


def test_proxy_routes_fallback(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    import json

    r = client.get("/configs/edenai/providers/ocr/ocr")
    d = json.loads(r.get_data(as_text=True))
    assert d["source"] == "fallback" and len(d["providers"]) > 0

    r = client.get("/configs/edenai/llms")
    d = json.loads(r.get_data(as_text=True))
    assert d["source"] == "fallback" and len(d["providers"]) > 0


def test_proxy_routes_require_admin(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")
    r = client.get("/configs/edenai/llms", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()
