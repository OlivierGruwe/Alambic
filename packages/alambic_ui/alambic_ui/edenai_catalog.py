"""
alambic_ui.edenai_catalog — listing des providers/modèles EdenAI.

Reprend le proxy de FlowerScan : le backend interroge le catalogue EdenAI et
renvoie la liste au front. L'appel est ANONYME (le catalogue /v3/info est
public, pas besoin de clé) et le feature/subtype sont passés dans le CHEMIN :
  - providers : GET {EU}/v3/info/{feature}/{subtype}
  - modèles   : GET {EU}/v3/llm/models

Endpoint EU par souveraineté (api.eu.edenai.run). Si l'appel échoue (réseau,
format inattendu), on retombe sur les listes statiques de config_schema — l'UI
reste fonctionnelle.
"""

from __future__ import annotations

import logging

from .config_schema import (
    LLM_MODELS,
    LLM_PROVIDERS,
    OCR_PROVIDERS,
)

logger = logging.getLogger(__name__)

# Endpoint EU (résidence des données en Europe).
EU_BASE = "https://api.eu.edenai.run"

# Listes statiques de secours (par usage).
_FALLBACK = {
    "ocr": {"providers": OCR_PROVIDERS, "models": []},
    "llm": {"providers": LLM_PROVIDERS, "models": LLM_MODELS},
    "text": {"providers": LLM_PROVIDERS, "models": LLM_MODELS},
}


def list_providers(feature: str, subtype: str, *, timeout: int = 10) -> dict:
    """Liste les providers d'une feature/subtype via {EU}/v3/info/{feature}/{subtype}.

    Appel anonyme (catalogue public). Renvoie
    {"providers": [...], "models": [...], "source": "edenai"|"fallback"}.
    Ne lève pas : repli sur le fallback en cas d'erreur.
    """
    fallback = _FALLBACK.get(feature, {"providers": [], "models": []})
    try:
        import requests

        resp = requests.get(
            f"{EU_BASE}/v3/info/{feature}/{subtype}",
            timeout=timeout,
        )
        resp.raise_for_status()
        providers, models = _parse_info(resp.json())
        if not providers:
            return {**fallback, "source": "fallback"}
        return {"providers": providers, "models": models, "source": "edenai"}
    except Exception:
        logger.exception("EdenAI list_providers a échoué (feature=%s)", feature)
        return {**fallback, "source": "fallback"}


def list_llms(*, timeout: int = 10) -> dict:
    """Liste les modèles LLM via {EU}/v3/llm/models (appel anonyme).

    Ne garde que les modèles utilisables pour l'extraction structurée
    (capabilities.supports_response_schema == true). Le filtrage par RÉGION est
    délégué au front (sélecteur global) : on renvoie, pour chaque modèle, la
    liste de ses régions, plus la liste de toutes les régions rencontrées.

    Renvoie {
      "by_provider": {provider: [{"name": str, "regions": [codes]}]},
      "providers": [...], "regions": [...], "source": ...
    }. Repli sur la liste statique structurée.
    """
    try:
        import requests

        resp = requests.get(f"{EU_BASE}/v3/llm/models", timeout=timeout)
        resp.raise_for_status()
        by_provider, regions = _parse_llm_by_provider(resp.json())
        if not by_provider:
            return {**_fallback_llm(), "source": "fallback"}
        return {
            "by_provider": by_provider,
            "providers": sorted(by_provider),
            "regions": sorted(regions),
            "source": "edenai",
        }
    except Exception:
        logger.exception("EdenAI list_llms a échoué")
        return {**_fallback_llm(), "source": "fallback"}


def _fallback_llm() -> dict:
    """Liste statique structurée par provider (secours), régions = ['eu']."""
    by_provider: dict[str, list[dict]] = {}
    for full in LLM_MODELS:
        if "/" in full:
            prov, model = full.split("/", 1)
        else:
            prov, model = "mistral", full
        by_provider.setdefault(prov, []).append({"name": model, "regions": ["eu"]})
    for prov in LLM_PROVIDERS:
        by_provider.setdefault(prov, [])
    return {
        "by_provider": by_provider,
        "providers": sorted(by_provider),
        "regions": ["eu"],
    }


def _parse_llm_by_provider(payload) -> tuple[dict, set]:
    """Construit ({provider: [{name, regions}]}, {toutes_régions}).

    Filtre uniquement sur supports_response_schema (le filtrage région est fait
    côté front). Provider = owned_by, nom = model_name (propre).
    """
    items = (
        payload.get("data", [])
        if isinstance(payload, dict)
        else (payload if isinstance(payload, list) else [])
    )

    by_provider: dict[str, dict[str, list]] = {}
    all_regions: set[str] = set()
    for m in items:
        if not isinstance(m, dict):
            continue
        caps = m.get("capabilities") or {}
        if not caps.get("supports_response_schema"):
            continue
        provider = m.get("owned_by") or ""
        model_name = m.get("model_name") or m.get("id") or ""
        if not (provider and model_name):
            continue
        regions = [(r.get("code") if isinstance(r, dict) else r) for r in (m.get("regions") or [])]
        regions = [r for r in regions if r]
        all_regions.update(regions)
        prov_models = by_provider.setdefault(str(provider), {})
        existing = prov_models.setdefault(str(model_name), [])
        for r in regions:
            if r not in existing:
                existing.append(r)

    result = {
        prov: [{"name": name, "regions": sorted(regs)} for name, regs in sorted(models.items())]
        for prov, models in by_provider.items()
    }
    return result, all_regions


def _parse_info(payload) -> tuple[list, list]:
    """Extrait providers/modèles d'une réponse /v3/info/{feature}/{subtype}.

    Parsing défensif : tolère une liste d'entrées ou un dict {items/data: [...]}.
    """
    providers: set[str] = set()
    models: set[str] = set()

    entries = []
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        for key in ("items", "data", "results", "providers"):
            if isinstance(payload.get(key), list):
                entries = payload[key]
                break

    for e in entries:
        if isinstance(e, str):
            providers.add(e)
        elif isinstance(e, dict):
            prov = e.get("provider") or e.get("name")
            if prov:
                providers.add(str(prov))
            model = e.get("model")
            if model and prov:
                models.add(f"{prov}/{model}")

    return sorted(providers), sorted(models)


