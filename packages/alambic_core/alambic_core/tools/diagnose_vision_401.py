"""Diagnostic du 401 EdenAI sur la détection multi-document (appel vision).

Usage (dans un conteneur worker, avec l'environnement Alambic chargé) :

    python -m alambic_core.tools.diagnose_vision_401 <config_id>

Le script résout la clé EdenAI EXACTEMENT comme le worker multidoc, affiche l'état
des réglages (clé présente ? région ? provider/modèle vision ?), puis tente un
appel vision minimal pour faire remonter le message d'erreur réel d'EdenAI.

Aucune donnée sensible n'est affichée en clair : la clé est masquée.
"""

from __future__ import annotations

import sys


def _mask(secret: str) -> str:
    if not secret:
        return "(VIDE)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]} (len={len(secret)})"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage : python -m alambic_core.tools.diagnose_vision_401 <config_id>")
        return 2

    config_id = sys.argv[1]

    from alambic_core.ai.edenai_endpoints import endpoint_for, normalize_llm_endpoint
    from alambic_core.ai.edenai_ocr import resolve_edenai_secret
    from alambic_core.db.session import init_core, session_scope
    from alambic_core.models import Config

    # Initialise le core (engine + sessionmaker) si ce n'est pas déjà fait.
    init_core()

    with session_scope() as s:
        config = s.get(Config, config_id)
        if config is None:
            print(f"✗ Config introuvable : {config_id}")
            return 1

        settings = config.edenai_settings or {}
        secret = resolve_edenai_secret(config)
        region = settings.get("region", "")
        provider = settings.get("vision_llm_provider") or "mistral (défaut)"
        model = settings.get("vision_llm_model") or "pixtral-large-latest (défaut)"
        custom_endpoint = settings.get("vision_end_point") or ""

        print("─── Réglages de la config ─────────────────────────────")
        print(f"  config_id        : {config_id}")
        print(f"  multi_doc_detect : {config.multi_doc_detect}")
        print(f"  clé EdenAI       : {_mask(secret)}")
        print(f"  région           : {region or '(vide → défaut)'}")
        print(f"  vision provider  : {provider}")
        print(f"  vision modèle    : {model}")
        print(f"  edenai_settings  : {len(settings)} clé(s) "
              f"{'⚠ VIDE — config probablement effacée' if not settings else ''}")

        endpoint = normalize_llm_endpoint(custom_endpoint or endpoint_for("vision", region))
        print(f"  endpoint vision  : {endpoint}")
        print()

        if not secret:
            print("✗ DIAGNOSTIC : la clé EdenAI résolue est VIDE.")
            print("  → Renseigner la clé sur le compte ou la config (le bug historique")
            print("    de edenai_settings vidé pouvait effacer ces réglages).")
            return 1

        # Appel vision minimal (image 1x1) pour récupérer l'erreur réelle.
        provider_str = settings.get("vision_llm_provider") or "mistral"
        model_str_name = settings.get("vision_llm_model") or "pixtral-large-latest"
        model_str = f"{provider_str}/{model_str_name}"

    # 1x1 PNG transparent (base64), hors session.
    px = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    import requests

    print("─── Test d'appel vision EdenAI ────────────────────────")
    try:
        resp = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_str,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "ping"},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{px}"},
                            },
                        ],
                    }
                ],
            },
            timeout=30,
        )
        print(f"  HTTP {resp.status_code}")
        body = resp.text[:600]
        print(f"  Réponse : {body}")
        print()
        if resp.status_code == 401:
            print("✗ DIAGNOSTIC : 401 Unauthorized malgré une clé présente.")
            print("  Causes possibles, par ordre de probabilité :")
            print("  1. Clé invalide/expirée/révoquée côté EdenAI.")
            print("  2. Clé sans droit sur la feature LLM (llm/chat/completions)")
            print(f"     ou sur le provider « {provider_str} » (à activer dans EdenAI).")
            print("  3. Mauvaise région : la clé d'une région ne marche pas sur une autre.")
            print("     (endpoint utilisé ci-dessus ↑ — vérifier qu'il correspond à ton compte)")
        elif resp.status_code == 200:
            print("✓ La clé fonctionne sur l'endpoint vision. Le 401 observé venait")
            print("  probablement d'une config DIFFÉRENTE (clé vidée sur celle-là).")
        else:
            print(f"⚠ Statut inattendu {resp.status_code} — lire le corps ci-dessus.")
    except requests.RequestException as exc:
        print(f"✗ Erreur réseau : {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
