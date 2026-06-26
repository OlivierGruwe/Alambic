"""Fixtures de test pour alambic_ui : app Flask + base SQLite + super-admin."""

from __future__ import annotations

import os
import re
import tempfile

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def app_ctx():
    """App Flask sur une base SQLite jetable, avec un super-admin et un validateur."""
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()
    os.environ["ALAMBIC_UI_SECRET_KEY"] = "test-secret"

    import alambic_core.models  # noqa: F401
    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker
    from alambic_core.domain.enums import UserRole
    from alambic_core.models import User
    from alambic_core.security.passwords import hash_password

    import alambic_ui

    app = alambic_ui.create_app()
    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()
    with Sess() as s:
        s.add(
            User(
                id="admin",
                email="admin@arondor.com",
                role=UserRole.SUPER_ADMIN.value,
                password_hash=hash_password("MotDePasse1"),
                active=True,
            )
        )
        s.add(
            User(
                id="val",
                email="val@arondor.com",
                role=UserRole.VALIDATOR.value,
                password_hash=hash_password("MotDePasse1"),
                active=True,
            )
        )
        s.commit()

    yield app, Sess
    os.unlink(dbfile)


def csrf(html: str) -> str | None:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def login(client, email="admin@arondor.com", password="MotDePasse1"):
    tok = csrf(client.get("/login").get_data(as_text=True))
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": tok},
        follow_redirects=True,
    )
