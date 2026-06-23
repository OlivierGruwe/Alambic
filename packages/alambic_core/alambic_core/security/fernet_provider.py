"""
alambic_core.security.fernet_provider — implémentation par défaut (Fernet).

Fernet = AES-128-CBC + HMAC-SHA256 (chiffrement authentifié). Toute altération
du texte chiffré est détectée au déchiffrement (InvalidToken). C'est le standard
recommandé de la lib `cryptography`.

La clé maître vient de l'environnement (ALAMBIC_SECRET_KEY), jamais en dur.
En prod Swarm : injectée via un Docker secret. Génération d'une clé :
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .provider import SecretProvider


class FernetSecretProvider(SecretProvider):
    """Chiffrement Fernet à clé maître.

    Supporte la ROTATION de clé : on peut passer plusieurs clés. La première
    chiffre ; toutes servent à déchiffrer (les anciennes restent lisibles le
    temps de ré-encrypter). C'est le pattern MultiFernet.
    """

    def __init__(self, keys: list[str] | str):
        if isinstance(keys, str):
            keys = [keys]
        if not keys or not keys[0]:
            raise ValueError("Au moins une clé Fernet est requise (ALAMBIC_SECRET_KEY).")
        self._fernet = MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            plaintext = ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if ciphertext is None or ciphertext == "":
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            # On ne réémet PAS le texte chiffré dans le message (fuite potentielle).
            raise ValueError("Déchiffrement impossible : clé invalide ou donnée altérée.") from exc


class NullSecretProvider(SecretProvider):
    """Provider no-op pour les tests unitaires : renvoie la valeur telle quelle.

    Permet de tester la logique métier sans gérer de clés. À NE JAMAIS utiliser
    en production (les secrets seraient stockés en clair).
    """

    def encrypt(self, plaintext: str) -> str:
        return plaintext or ""

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext or ""
