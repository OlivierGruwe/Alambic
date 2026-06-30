"""
alambic_core.db.types — types de colonnes custom.

EncryptedString : une colonne dont la valeur est chiffrée AU REPOS (en base)
et déchiffrée à la lecture, de façon transparente pour le code métier.
Remplace le mécanisme __encrypted_fields__ de flowerscan_lib.

Usage dans un modèle :
    ftp_password: Mapped[str] = mapped_column(EncryptedString(), default="")

Le provider de chiffrement est résolu via un registre global (set_secret_provider),
pour éviter de l'injecter dans chaque définition de colonne. En tests, on pose
un NullSecretProvider ; en prod, le FernetSecretProvider.
"""

from __future__ import annotations

from sqlalchemy import String, TypeDecorator

from ..security.provider import SecretProvider

# Registre global du provider courant. Posé une fois au démarrage de l'app
# (ou du test) via set_secret_provider().
_PROVIDER: SecretProvider | None = None


def set_secret_provider(provider: SecretProvider) -> None:
    """Enregistre le provider de chiffrement utilisé par EncryptedString."""
    global _PROVIDER
    _PROVIDER = provider


def get_secret_provider() -> SecretProvider:
    if _PROVIDER is None:
        raise RuntimeError(
            "Aucun SecretProvider configuré. Appelle set_secret_provider() "
            "au démarrage (FernetSecretProvider en prod, NullSecretProvider en test)."
        )
    return _PROVIDER


class EncryptedString(TypeDecorator):
    """Colonne texte chiffrée au repos.

    process_bind_param  : appelé à l'ÉCRITURE → chiffre.
    process_result_value: appelé à la LECTURE → déchiffre.
    Le code métier ne voit JAMAIS le texte chiffré : il lit/écrit du clair.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return get_secret_provider().encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return get_secret_provider().decrypt(value)
