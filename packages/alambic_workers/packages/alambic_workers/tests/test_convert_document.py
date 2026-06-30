"""Test de la tâche convert_document (conversion + MAJ DB + step)."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
def core_db(monkeypatch):
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Document, Transaction

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()
    with Sess() as s:
        s.add(Transaction(id="tx1", status="WORKING", process="DOC_EXTRACTED"))
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="CREATED",
                process="DOC_EXTRACTED",
                bucket_name="alambic-work",
                object_key="__transactions__/a/c/tx1/d1.txt",
            )
        )
        s.commit()

    # Source texte locale + mock storage.
    src = tempfile.mktemp(suffix=".txt")
    Path(src).write_text("Permis AM Marilou.\nLigne éàü.\n" * 3, encoding="utf-8")

    import alambic_workers.tasks.conversion as conv

    monkeypatch.setattr(conv.storage, "download_to", lambda b, k, d: (shutil.copy(src, d), d)[1])
    monkeypatch.setattr(conv.storage, "put_object", lambda b, k, body, metadata=None: None)

    yield Sess
    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_convert_document_text(core_db):
    from alambic_core.models import Document, TransactionStep

    from alambic_workers.tasks.conversion import convert_document

    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {
            "documentId": "d1",
            "file": {"bucket": "alambic-work", "key": "__transactions__/a/c/tx1/d1.txt"},
        },
    }
    out = convert_document(payload)
    assert out["document"]["file"]["key"].endswith(".pdf")
    assert out["nb_pages"] >= 1
    with core_db() as s:
        d = s.get(Document, "d1")
        assert d.status == "CONVERTED_TO_PDF"
        assert d.object_key.endswith(".pdf")
        steps = s.query(TransactionStep).filter_by(process="FILE_CONVERTED").all()
        assert len(steps) == 1


def test_convert_document_unknown_discards(core_db, monkeypatch):
    from alambic_core.models import Document, Message, Transaction

    import alambic_workers.tasks.conversion as conv

    # Source d'un type non convertible.
    src = tempfile.mktemp(suffix=".xyz")
    Path(src).write_bytes(b"\x00\x01\x02random")
    monkeypatch.setattr(conv.storage, "download_to", lambda b, k, d: (shutil.copy(src, d), d)[1])
    with core_db() as s:
        d = s.get(Document, "d1")
        d.object_key = "__transactions__/a/c/tx1/d1.xyz"
        s.commit()

    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {
            "documentId": "d1",
            "file": {"bucket": "alambic-work", "key": "__transactions__/a/c/tx1/d1.xyz"},
        },
    }
    out = conv.convert_document(payload)
    assert out["document"] is None
    with core_db() as s:
        d = s.get(Document, "d1")
        assert d.status == "DISCARDED"
        assert d.discard_reason
        tx = s.get(Transaction, "tx1")
        assert tx.nb_discarded == 1
        msgs = s.query(Message).filter_by(level="WARNING").all()
        assert len(msgs) == 1
