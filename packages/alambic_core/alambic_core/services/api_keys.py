"""alambic_core.services.api_keys — génération et vérification des clés API.

Logique métier réutilisable par l'UI (création/révocation) et par les endpoints
WS (authentification). La valeur en clair d'une clé n'existe qu'au moment de sa
génération ; ensuite, seul son hash est connu du système.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Préfixe des clés Alambic (aide à les reconnaître : « alb_… »).
KEY_PREFIX = "alb_"
# Longueur de la partie aléatoire (caractères url-safe).
_RANDOM_LEN = 40
# Longueur du préfixe non secret conservé pour l'affichage.
_DISPLAY_PREFIX_LEN = 12


@dataclass
class GeneratedKey:
    """Résultat d'une génération de clé : valeur en clair (une seule fois) + métadonnées."""

    plaintext: str  # à afficher UNE fois ; non stocké
    key_hash: str
    key_prefix: str


def hash_key(plaintext: str) -> str:
    """Hash SHA-256 (hex) d'une valeur de clé. Déterministe (pour la vérification)."""
    return hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()


def generate_key() -> GeneratedKey:
    """Génère une nouvelle clé API : valeur en clair + hash + préfixe d'affichage."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(_RANDOM_LEN)
    return GeneratedKey(
        plaintext=plaintext,
        key_hash=hash_key(plaintext),
        key_prefix=plaintext[:_DISPLAY_PREFIX_LEN],
    )


def expiry_from_days(days: int | None, *, now: datetime | None = None) -> datetime | None:
    """Date d'expiration depuis une validité en jours. 0/None => sans limite."""
    if not days or int(days) <= 0:
        return None
    return (now or datetime.now(UTC)) + timedelta(days=int(days))


def verify_key(session, plaintext: str):
    """Retourne l'ApiKey valide correspondant à une valeur présentée, ou None.

    Vérifie le hash, l'activation et l'expiration. Aucune valeur en clair n'est
    lue en base : on compare des hash.
    """
    from alambic_core.models import ApiKey

    if not plaintext:
        return None
    digest = hash_key(plaintext)
    key = session.query(ApiKey).filter(ApiKey.key_hash == digest).one_or_none()
    if key is None or not key.is_valid_now():
        return None
    return key
