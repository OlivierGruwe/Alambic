"""Tests du mécanisme step (MAJ DB, journal, skip, erreur)."""

from __future__ import annotations

import os
import tempfile
import time

import pytest
from cryptography.fernet import Fernet

from alambic_core.models import Document, Message, Transaction, TransactionStep
from alambic_core.pipeline import is_already_past, step, step_rank


@pytest.fixture
def core_db():
    """Initialise le core sur une base SQLite fichier (step utilise session_scope,
    donc le sessionmaker global doit être configuré)."""
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    try:

        @compiles(JSONB, "sqlite")
        def _j(e, c, **k):  # noqa: ANN001
            return "JSON"
    except Exception:
        pass

    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()
    with Sess() as s:
        s.add(Transaction(id="tx1", status="WORKING", process="NEWDOC"))
        s.add(Document(id="d1", transaction_id="tx1", status="CREATED", process="DOC_CREATED"))
        s.commit()
    yield Sess

    # Windows verrouille le fichier SQLite tant que le moteur tient une
    # connexion : dispose le moteur avant suppression, et tolère l'échec.
    from contextlib import suppress

    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_step_rank_order():
    assert step_rank("NEWDOC") < step_rank("FILEEXTRACTOR")
    assert step_rank("OCR_DONE") < step_rank("CLASSIFIER")
    assert step_rank("inconnu") is None


def test_is_already_past():
    assert is_already_past("DOC_CREATED", "NEWDOC") is True
    assert is_already_past("NEWDOC", "FILEEXTRACTOR") is False
    assert is_already_past("inconnu", "NEWDOC") is False


def test_step_executes_and_journals(core_db):
    with step("tx1", "DOC_CREATED") as st:
        assert not st.skipped
        time.sleep(0.01)
    with core_db() as s:
        tx = s.get(Transaction, "tx1")
        steps = s.query(TransactionStep).filter_by(transaction_id="tx1").all()
        assert tx.process == "DOC_CREATED"
        assert tx.process_time is not None
        assert len(steps) == 1
        assert steps[0].status == "OK"
        assert steps[0].duration_ms >= 10


def test_step_skips_when_already_past(core_db):
    with step("tx1", "NEWDOC", document_id="d1") as st:
        assert st.skipped
    with core_db() as s:
        steps = s.query(TransactionStep).filter_by(process="NEWDOC").all()
        assert steps == []


def test_step_updates_document(core_db):
    with step("tx1", "FILEEXTRACTOR", document_id="d1") as st:
        assert not st.skipped
    with core_db() as s:
        doc = s.get(Document, "d1")
        assert doc.process == "FILEEXTRACTOR"
        assert doc.process_time is not None


def test_step_document_does_not_touch_transaction(core_db):
    # Non-régression : une étape par-document ne doit PAS mettre à jour la
    # transaction (sinon conflit de version optimiste quand N documents
    # franchissent l'étape en parallèle → StaleDataError).
    from alambic_core.models import Transaction

    with core_db() as s:
        tx_before = s.get(Transaction, "tx1")
        process_before = tx_before.process
        version_before = tx_before.version

    with step("tx1", "FILE_CONVERTED", document_id="d1") as st:
        assert not st.skipped

    with core_db() as s:
        tx = s.get(Transaction, "tx1")
        # La transaction n'a pas bougé : ni process, ni version.
        assert tx.process == process_before
        assert tx.version == version_before
        # Mais le document, lui, a bien avancé.
        assert s.get(Document, "d1").process == "FILE_CONVERTED"


def test_step_error_journals_and_raises(core_db):
    with pytest.raises(ValueError), step("tx1", "OCR_DONE", document_id="d1"):
        raise ValueError("boom")
    with core_db() as s:
        err = s.query(TransactionStep).filter_by(status="ERROR").all()
        msgs = s.query(Message).filter_by(level="ERROR").all()
        assert len(err) == 1
        assert "boom" in err[0].detail
        assert len(msgs) == 1
        assert msgs[0].transaction_id == "tx1"
        assert "OCR_DONE" in msgs[0].text
