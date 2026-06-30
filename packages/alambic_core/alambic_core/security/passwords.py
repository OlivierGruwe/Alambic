"""
alambic_core.security.passwords — hachage de mots de passe (argon2id).

argon2id est l'algorithme recommandé aujourd'hui (lauréat de la Password Hashing
Competition) : résistant aux attaques GPU et aux canaux auxiliaires. On ne stocke
JAMAIS le mot de passe en clair, seulement le hash (qui inclut le sel et les
paramètres de coût).

Un hash argon2 n'est pas un secret réversible : pas besoin de le chiffrer en base
(contrairement aux secrets Fernet). On le compare via verify_password.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Paramètres par défaut d'argon2-cffi (m=64MiB, t=3, p=4) : bon compromis
# sécurité/perf en 2026. Un seul hasher réutilisé (thread-safe).
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Renvoie le hash argon2id d'un mot de passe en clair."""
    if not plain:
        raise ValueError("Le mot de passe ne peut pas être vide.")
    return _hasher.hash(plain)


def verify_password(password_hash: str, plain: str) -> bool:
    """Vérifie qu'un mot de passe en clair correspond au hash. False si non."""
    if not password_hash or not plain:
        return False
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True si le hash a été produit avec des paramètres obsolètes (à re-hacher).

    À appeler après un verify réussi : si True, re-hacher le mot de passe en clair
    et mettre à jour la base (montée en sécurité transparente).
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False
