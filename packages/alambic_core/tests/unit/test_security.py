"""Tests unitaires de la couche sécurité (chiffrement des secrets)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from alambic_core.security.fernet_provider import FernetSecretProvider, NullSecretProvider


@pytest.fixture
def provider():
    return FernetSecretProvider(Fernet.generate_key().decode())


def test_round_trip(provider):
    secret = "ftp_password_sensible_123"
    assert provider.decrypt(provider.encrypt(secret)) == secret


def test_ciphertext_is_not_plaintext(provider):
    enc = provider.encrypt("secret")
    assert enc != "secret"
    assert enc.startswith("gAAAA")  # préfixe d'un token Fernet


def test_non_deterministic(provider):
    """Deux chiffrements de la même valeur diffèrent (IV aléatoire) —
    propriété de sécurité, pas un bug."""
    a, b = provider.encrypt("x"), provider.encrypt("x")
    assert a != b
    assert provider.decrypt(a) == provider.decrypt(b) == "x"


def test_tampering_detected(provider):
    enc = provider.encrypt("secret")
    tampered = enc[:-4] + "XXXX"
    with pytest.raises(ValueError):
        provider.decrypt(tampered)


def test_empty_values(provider):
    assert provider.decrypt("") == ""
    assert provider.decrypt(provider.encrypt("")) == ""


def test_key_rotation():
    """Une nouvelle clé en tête, l'ancienne en second : les données chiffrées
    avec l'ancienne restent déchiffrables (MultiFernet)."""
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    enc_with_old = FernetSecretProvider(old).encrypt("data")
    rotated = FernetSecretProvider([new, old])
    assert rotated.decrypt(enc_with_old) == "data"


def test_empty_key_rejected():
    with pytest.raises(ValueError):
        FernetSecretProvider("")


def test_null_provider_passthrough():
    n = NullSecretProvider()
    assert n.encrypt("x") == "x"
    assert n.decrypt("x") == "x"
