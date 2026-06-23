"""
Fixtures partagées des tests alambic_core.

Les tests UNITAIRES tournent sur SQLite en mémoire (rapide, sans Docker).
JSONB n'existant pas en SQLite, on le compile vers JSON pour ces tests ;
les tests d'INTÉGRATION (testcontainers) valideront le JSONB/GIN réel.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    """JSONB → JSON sous SQLite (tests unitaires uniquement)."""
    return compiler.visit_JSON(JSON(), **kw)


@pytest.fixture(scope="session", autouse=True)
def _secret_provider():
    """Configure un provider Fernet réel pour toute la session de test.

    autouse=True : posé automatiquement, car EncryptedString en a besoin dès
    qu'un modèle avec colonne chiffrée est instancié.
    """
    from alambic_core.db.types import set_secret_provider
    from alambic_core.security.fernet_provider import FernetSecretProvider

    set_secret_provider(FernetSecretProvider(Fernet.generate_key().decode()))


@pytest.fixture
def engine():
    """Moteur SQLite en mémoire, schéma créé/détruit par test."""
    from alambic_core.db.base import Base
    import alambic_core.models  # noqa: F401 — enregistre les modèles sur Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    """Session liée au moteur de test."""
    with Session(engine) as s:
        yield s
