"""
Tests du Repo (façade alambic_core utilisée par l'orchestrateur d'ingestion).

Valident que les méthodes appelées par orchestration/ingestion.py écrivent bien
dans le schéma réel d'alambic_core. SQLite en mémoire, rapides (pas de Docker).
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.security.fernet_provider import FernetSecretProvider
import alambic_core.models  # noqa: F401 — enregistre les modèles
from alambic_core.models import Account, Document, Transaction
from alambic_core.repositories import (
    DocumentIndexRepository,
    MessageRepository,
)

from core.repo import Repo


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
        yield s


@pytest.fixture
def seeded(session):
    """Un account + une transaction prêts, comme après CreateTransaction."""
    acc = Account(account_name="ACME")
    tx = Transaction(id="trx-1", status="WORKING", account=acc)
    session.add_all([acc, tx])
    session.commit()
    return session


def test_upsert_document_creates(seeded):
    repo = Repo(session=seeded)
    repo.upsert_document("doc-1", "trx-1", {"bucket": "alambic-input", "key": "f.pdf"})
    seeded.commit()
    doc = seeded.get(Document, "doc-1")
    assert doc is not None
    assert doc.status == "CREATED"
    assert doc.bucket_name == "alambic-input"
    assert doc.object_key == "f.pdf"


def test_upsert_document_is_idempotent(seeded):
    repo = Repo(session=seeded)
    repo.upsert_document("doc-1", "trx-1", {"bucket": "b", "key": "k1"})
    seeded.commit()
    repo.upsert_document("doc-1", "trx-1", {"bucket": "b", "key": "k2"})
    seeded.commit()
    docs = [d for d in seeded.query(Document).all() if d.id == "doc-1"]
    assert len(docs) == 1
    assert docs[0].object_key == "k2"


def test_put_metadata_index_and_idempotence(seeded):
    Repo(session=seeded).upsert_document("doc-1", "trx-1", {"bucket": "b", "key": "k"})
    seeded.commit()
    repo = Repo(session=seeded)
    repo.put_metadata_index("doc-1", "client", "ACME")
    seeded.commit()
    repo.put_metadata_index("doc-1", "client", "ACME-CORP")
    seeded.commit()
    idx = DocumentIndexRepository(seeded).metadata_of("doc-1")
    assert len(idx) == 1
    assert idx[0].index_value == "ACME-CORP"


def test_update_transaction(seeded):
    Repo(session=seeded).update_transaction("trx-1", status="COMPLETED", process="DISPATCH_DONE")
    seeded.commit()
    tx = seeded.get(Transaction, "trx-1")
    assert tx.status == "COMPLETED"
    assert tx.process == "DISPATCH_DONE"


def test_add_message(seeded):
    Repo(session=seeded).add_message("trx-1", "ERROR", "01_Ingestion", "boom")
    seeded.commit()
    msgs = MessageRepository(seeded).for_transaction("trx-1")
    assert len(msgs) == 1
    assert msgs[0].level == "ERROR"
    assert msgs[0].text == "boom"


def test_mark_document_error(seeded):
    Repo(session=seeded).upsert_document("doc-1", "trx-1", {"bucket": "b", "key": "k"})
    seeded.commit()
    Repo(session=seeded).mark_document_error("doc-1")
    seeded.commit()
    assert seeded.get(Document, "doc-1").status == "ERROR"
