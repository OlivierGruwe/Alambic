"""
Tests du garde-fou d'optimistic_object : un id métier vide/None doit être refusé
(pour empêcher la création d'objets fantômes avec un id auto-généré).
"""

from __future__ import annotations

import alambic_core.models  # noqa: F401
import pytest
from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.models import Account, Document, Transaction
from alambic_core.security.fernet_provider import FernetSecretProvider
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alambic_workers.optimistic import optimistic_object


@compiles(JSONB, "sqlite")
def _jsonb_as_json_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def _provider():
    set_secret_provider(FernetSecretProvider(Fernet.generate_key().decode()))


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add_all([Account(account_name="A"), Transaction(id="trx-1", status="W")])
        s.commit()
        yield s


@pytest.mark.parametrize("bad_id", [None, "", 0])
def test_optimistic_object_rejects_empty_id(session, bad_id):
    with (
        pytest.raises(ValueError, match="id requis"),
        optimistic_object(Document, bad_id, session=session),
    ):
        pass


def test_optimistic_object_creates_with_valid_id(session):
    with optimistic_object(Document, "doc-ok", session=session) as doc:
        doc.transaction_id = "trx-1"
        doc.status = "OK"
    session.commit()
    assert session.get(Document, "doc-ok") is not None


def test_optimistic_object_updates_existing(session):
    session.add(Document(id="doc-x", transaction_id="trx-1", status="WORKING"))
    session.commit()
    with optimistic_object(Document, "doc-x", session=session) as doc:
        doc.status = "DONE"
    session.commit()
    assert session.get(Document, "doc-x").status == "DONE"
