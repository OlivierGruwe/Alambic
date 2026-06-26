"""
Tests de la brique authentification d'alambic_core.

Couvre : hachage argon2 (passwords), modèle User (rôles, normalisation email),
UserRepository (by_email, has_any_super_admin), LocalAuthProvider (tous les
chemins d'authentification). SQLite en mémoire, sans Docker.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import alambic_core.models  # noqa: F401
from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.domain.enums import UserRole
from alambic_core.models import User
from alambic_core.repositories import UserRepository
from alambic_core.security.auth import LocalAuthProvider
from alambic_core.security.fernet_provider import FernetSecretProvider
from alambic_core.security.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def _provider():
    set_secret_provider(FernetSecretProvider(Fernet.generate_key().decode()))


@pytest.fixture
def sessions():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


# ── passwords (argon2) ───────────────────────────────────────────────────────
def test_hash_and_verify():
    h = hash_password("S3cret!")
    assert h != "S3cret!"
    assert verify_password(h, "S3cret!")
    assert not verify_password(h, "wrong")


def test_verify_empty_inputs():
    h = hash_password("x")
    assert not verify_password(h, "")
    assert not verify_password("", "x")


def test_hash_empty_raises():
    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash_on_valid_hash():
    assert needs_rehash(hash_password("x")) is False


# ── modèle User ──────────────────────────────────────────────────────────────
def test_email_normalized_on_write(sessions):
    with sessions() as s:
        s.add(User(id="u1", email="  Boss@Arondor.COM ", role=UserRole.ADMIN.value))
        s.commit()
        assert s.get(User, "u1").email == "boss@arondor.com"


def test_role_helpers():
    sa = User(id="sa", email="sa@x.fr", role=UserRole.SUPER_ADMIN.value)
    ad = User(id="ad", email="ad@x.fr", role=UserRole.ADMIN.value)
    va = User(id="va", email="va@x.fr", role=UserRole.VALIDATOR.value)
    assert sa.is_super_admin and sa.is_admin and not sa.is_validator
    assert not ad.is_super_admin and ad.is_admin
    assert va.is_validator and not va.is_admin


def test_email_unique(sessions):
    with sessions() as s:
        s.add(User(id="u1", email="dup@x.fr", role=UserRole.ADMIN.value))
        s.commit()
        s.add(User(id="u2", email="dup@x.fr", role=UserRole.VALIDATOR.value))
        with pytest.raises(IntegrityError):
            s.commit()


# ── UserRepository ───────────────────────────────────────────────────────────
def test_by_email_normalizes(sessions):
    with sessions() as s:
        s.add(User(id="u1", email="user@x.fr", role=UserRole.ADMIN.value))
        s.commit()
        assert UserRepository(s).by_email("USER@X.FR").id == "u1"


def test_has_any_super_admin(sessions):
    with sessions() as s:
        repo = UserRepository(s)
        assert not repo.has_any_super_admin()
        s.add(User(id="u1", email="sa@x.fr", role=UserRole.SUPER_ADMIN.value))
        s.commit()
        assert repo.has_any_super_admin()


# ── LocalAuthProvider ────────────────────────────────────────────────────────
@pytest.fixture
def auth_setup(sessions):
    with sessions() as s:
        s.add(
            User(
                id="u1",
                email="admin@x.fr",
                role=UserRole.SUPER_ADMIN.value,
                password_hash=hash_password("good-pass"),
                active=True,
            )
        )
        s.add(
            User(
                id="u2",
                email="off@x.fr",
                role=UserRole.ADMIN.value,
                password_hash=hash_password("x"),
                active=False,
            )
        )
        s.add(
            User(
                id="u3",
                email="kc@x.fr",
                role=UserRole.VALIDATOR.value,
                auth_provider="keycloak",
                external_id="sub-1",
            )
        )
        s.commit()

    @contextmanager
    def factory():
        s = sessions()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    return LocalAuthProvider(factory)


def test_authenticate_success(auth_setup):
    u = auth_setup.authenticate("admin@x.fr", "good-pass")
    assert u is not None and u.is_super_admin


def test_authenticate_case_insensitive(auth_setup):
    assert auth_setup.authenticate("ADMIN@X.FR", "good-pass") is not None


def test_authenticate_wrong_password(auth_setup):
    assert auth_setup.authenticate("admin@x.fr", "nope") is None


def test_authenticate_inactive(auth_setup):
    assert auth_setup.authenticate("off@x.fr", "x") is None


def test_authenticate_delegated_account(auth_setup):
    # Compte keycloak : pas d'auth locale possible.
    assert auth_setup.authenticate("kc@x.fr", "whatever") is None


def test_get_user(auth_setup):
    assert auth_setup.get_user("u1").email == "admin@x.fr"
    assert auth_setup.get_user("nope") is None
