"""
alambic_core.security.provider — abstraction du chiffrement des secrets.

Remplace KMS (AWS). Le code métier appelle encrypt()/decrypt() sans connaître
l'implémentation concrète. Aujourd'hui : Fernet (clé maître locale). Demain :
on peut brancher Vault Transit en écrivant une autre implémentation de cette
interface, SANS toucher aux modèles ni aux repositories.

C'est le même principe que ton __encrypted_fields__ de flowerscan_lib, mais
l'algorithme de chiffrement est découplé derrière une interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecretProvider(ABC):
    """Contrat de chiffrement/déchiffrement des secrets au repos."""

    @abstractmethod
    def encrypt(self, plaintext: str) -> str:
        """Chiffre une valeur en clair → texte chiffré (str, stockable en base)."""
        ...

    @abstractmethod
    def decrypt(self, ciphertext: str) -> str:
        """Déchiffre → valeur en clair. Lève en cas d'altération (auth tag)."""
        ...
