"""Tests des briques B (doc racine) et C (extract_files).

Utilisent init_core (les tasks appellent session_scope via le mécanisme step),
donc une base SQLite fichier + storage Garage mocké.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
def core_and_storage(monkeypatch):
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    Base.metadata.create_all(get_engine())

    # Source zip locale + mock du storage.
    src_dir = tempfile.mkdtemp()
    src = os.path.join(src_dir, "arch.zip")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("f1.pdf", b"%PDF-1.4 a")
        z.writestr("f2.pdf", b"%PDF-1.4 b")
        z.writestr("v.mp4", b"\x00 video")

    import alambic_workers.tasks.ingestion as ing

    monkeypatch.setattr(ing.storage, "download_to", lambda b, k, d: (shutil.copy(src, d), d)[1])
    monkeypatch.setattr(ing.storage, "put_object", lambda b, k, body, metadata=None: None)

    yield get_sessionmaker()

    # Windows verrouille le fichier SQLite tant que le moteur tient une
    # connexion : on dispose le moteur avant de supprimer, et on tolère l'échec
    # de suppression (le fichier temp sera nettoyé par l'OS).
    from contextlib import suppress

    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _payload():
    return {
        "cid": "c1",
        "accountId": "acc1",
        "configId": "cfg1",
        "process": "STARTED",
        "transaction": {"transactionId": "trx1", "transaction_key": "k1"},
        "datas": [{"name": "origin", "value": "UI_IMPORT"}],
        "documents": [
            {
                "documentId": "doc1",
                "file": {
                    "bucket": "alambic-work",
                    "key": "__transactions__/acc1/cfg1/trx1/trx1.zip",
                },
            }
        ],
    }


def test_create_transaction_creates_root_document(core_and_storage):
    from alambic_core.models import Document, Transaction

    from alambic_workers.tasks.ingestion import create_transaction

    create_transaction(_payload())
    with core_and_storage() as s:
        tx = s.get(Transaction, "trx1")
        assert tx is not None and tx.process == "NEWDOC"
        doc = s.get(Document, "doc1")
        assert doc is not None
        assert doc.parent_id is None  # racine
        assert doc.transaction_id == "trx1"


def test_extract_files_creates_children_and_deprecates_parent(core_and_storage):
    from alambic_core.models import Document, Message, Transaction, TransactionStep

    from alambic_workers.tasks.ingestion import create_transaction, extract_files

    payload = _payload()
    create_transaction(payload)
    extract_files(payload)

    with core_and_storage() as s:
        root = s.get(Document, "doc1")
        assert root.status == "DEPRECATED"
        children = s.query(Document).filter_by(parent_id="doc1", status="CREATED").all()
        assert len(children) == 2
        discarded = s.query(Document).filter_by(status="DISCARDED").all()
        assert len(discarded) == 1
        assert discarded[0].discard_reason
        tx = s.get(Transaction, "trx1")
        assert tx.nb_discarded == 1
        msgs = s.query(Message).filter_by(level="WARNING").all()
        assert len(msgs) == 1
        steps = s.query(TransactionStep).filter_by(process="FILEEXTRACTOR").all()
        assert len(steps) == 1 and steps[0].status == "OK"


def test_extract_files_replayable(core_and_storage):
    """Rejouer extract_files ne duplique pas (skip de l'étape déjà passée)."""
    from alambic_core.models import Document

    from alambic_workers.tasks.ingestion import create_transaction, extract_files

    payload = _payload()
    create_transaction(payload)
    extract_files(payload)
    # Le doc racine est DEPRECATED ; rejouer doit être sauté (déjà au-delà).
    extract_files(payload)
    with core_and_storage() as s:
        children = s.query(Document).filter_by(parent_id="doc1").all()
        # 2 enfants actifs + 1 discarded = 3, pas le double.
        assert len(children) == 3
