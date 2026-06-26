"""Tests de la logique d'invitation (jetons, expiration, usage unique)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import alambic_core.models  # noqa: F401
from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.domain.enums import UserRole
from alambic_core.models import User
from alambic_core.security import invitations as inv
from alambic_core.security.fernet_provider import FernetSecretProvider
from alambic_core.security.passwords import verify_password


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def session():
    set_secret_provider(FernetSecretProvider(Fernet.generate_key().decode()))
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng)
    s = Sess()
    s.add(User(id="u1", email="invite@x.fr", role=UserRole.VALIDATOR.value, active=False))
    s.commit()
    return s


def test_issue_and_find(session):
    u = session.get(User, "u1")
    token = inv.issue_invitation(session, u)
    assert len(token) > 20
    assert inv.find_valid_invitation(session, token).id == "u1"


def test_unknown_token(session):
    assert inv.find_valid_invitation(session, "nope") is None


def test_accept_sets_password_and_consumes(session):
    u = session.get(User, "u1")
    token = inv.issue_invitation(session, u)
    user = inv.accept_invitation(session, token, "NewPass123")
    assert user is not None
    assert verify_password(user.password_hash, "NewPass123")
    assert user.active is True
    assert user.invite_token is None
    # Usage unique : le jeton ne marche plus.
    assert inv.find_valid_invitation(session, token) is None


def test_expired_token_rejected(session):
    u = session.get(User, "u1")
    token = inv.issue_invitation(session, u)
    u.invite_expires_at = datetime.now(UTC) - timedelta(days=1)
    session.commit()
    assert inv.find_valid_invitation(session, token) is None
    assert inv.accept_invitation(session, token, "x") is None
