"""alambic_core.ai.edenai_credits — solde de crédits EdenAI d'un compte.

Interroge l'endpoint cost_management d'EdenAI pour connaître les crédits restants
associés à une clé API. Ce solde est propre au compte EdenAI (abonnement) lié à
la clé ; dans Alambic, chaque compte a sa propre clé, donc son propre solde.

Un cache mémoire court (15 min par défaut) évite d'appeler EdenAI à chaque
affichage : le solde ne bouge pas assez vite pour justifier un appel par page.

Note : l'endpoint cost_management est un endpoint COMPTE (global v2), indépendant
de la région des features (OCR, etc.). On utilise donc api.edenai.run/v2/...
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("alambic.ai.credits")

# URL du solde de crédits (endpoint compte global, v2).
CREDITS_URL = "https://api.edenai.run/v2/cost_management/credits/"

# Durée de vie du cache (secondes). 15 min : compromis fraîcheur / charge.
CACHE_TTL_SECONDS = 15 * 60

# Cache mémoire : { hash_clé: (timestamp, CreditsResult) }.
_CACHE: dict[str, tuple[float, "CreditsResult"]] = {}


@dataclass
class CreditsResult:
    """Résultat d'une interrogation du solde EdenAI."""

    credits: float | None = None  # solde en $, None si indisponible
    ok: bool = False
    error: str = ""
    from_cache: bool = False


def _cache_key(secret_key: str) -> str:
    """Clé de cache : hash de la clé secrète (jamais la clé en clair en mémoire)."""
    return hashlib.sha256((secret_key or "").encode("utf-8")).hexdigest()


def clear_cache() -> None:
    """Vide le cache (utile en test ou après changement de clé)."""
    _CACHE.clear()


def get_credits(secret_key: str, *, use_cache: bool = True, timeout: int = 10) -> CreditsResult:
    """Récupère les crédits EdenAI restants pour une clé donnée.

    - use_cache : sert la valeur en cache si elle a moins de CACHE_TTL_SECONDS.
    - Ne lève jamais : en cas d'erreur (réseau, 401, format), renvoie ok=False
      avec un message, pour que l'UI dégrade proprement (affiche « indisponible »).
    """
    if not secret_key:
        return CreditsResult(ok=False, error="no_key")

    ckey = _cache_key(secret_key)
    now = time.monotonic()

    if use_cache:
        cached = _CACHE.get(ckey)
        if cached is not None and (now - cached[0]) < CACHE_TTL_SECONDS:
            res = cached[1]
            return CreditsResult(credits=res.credits, ok=res.ok, error=res.error, from_cache=True)

    try:
        import requests

        resp = requests.get(
            CREDITS_URL,
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("EdenAI crédits : échec réseau : %s", exc)
        return CreditsResult(ok=False, error=f"network_error: {exc}")

    if resp.status_code == 401:
        return CreditsResult(ok=False, error="unauthorized")
    if resp.status_code != 200:
        return CreditsResult(ok=False, error=f"http_{resp.status_code}")

    try:
        data = resp.json()
        credits = float(data.get("credits"))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("EdenAI crédits : réponse inattendue : %s", exc)
        return CreditsResult(ok=False, error="bad_response")

    result = CreditsResult(credits=credits, ok=True)
    _CACHE[ckey] = (now, result)
    return result
