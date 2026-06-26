"""Tests de la suppression complète d'une transaction (Garage work + cascade base)."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from contextlib import suppress

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
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

    # SQLite n'applique les FK cascade que si PRAGMA foreign_keys=ON.
    # (Postgres l'applique nativement — ce hook ne concerne que les tests SQLite.)
    @event.listens_for(get_engine(), "connect")
    def _fk_on(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(get_engine())

    # Garage mocké : on capture le préfixe supprimé.
    import alambic_core.services.deletion as de

    deleted = {"prefixes": []}
    monkeypatch.setattr(
        de.storage,
        "delete_prefix",
        lambda bucket, prefix: deleted["prefixes"].append(prefix) or 3,
    )
    monkeypatch.setattr(de.storage, "work_bucket", lambda: "alambic-work")

    yield get_sessionmaker(), deleted

    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _seed_full_transaction(Sess):
    from alambic_core.models import (
        Cost,
        Document,
        DocumentIndex,
        Message,
        Transaction,
        TransactionStep,
    )

    with Sess() as s:
        s.add(Transaction(id="tx1", status="COMPLETED", process="EXPORTED"))
        s.add(Document(id="d1", transaction_id="tx1", status="CREATED", object_key="k1"))
        s.add(
            Document(
                id="d2",
                transaction_id="tx1",
                parent_id="d1",
                status="DEPRECATED",
                object_key="k2",
            )
        )
        s.flush()
        s.add(
            DocumentIndex(
                document_id="d1",
                index_type="metadata",
                index_name="from",
                index_value="x",
            )
        )
        s.add(
            TransactionStep(
                transaction_id="tx1",
                process="FILEEXTRACTOR",
                status="OK",
                started_at=dt.datetime.now(dt.UTC),
            )
        )
        s.add(Message(transaction_id="tx1", level="INFO", source="t", text="m"))
        s.add(Cost(transaction_id="tx1"))
        s.commit()


def test_delete_transaction_cascades(core_db):
    from alambic_core.models import (
        Cost,
        Document,
        DocumentIndex,
        Message,
        Transaction,
        TransactionStep,
    )
    from alambic_core.services import delete_transaction

    Sess, deleted = core_db
    _seed_full_transaction(Sess)

    result = delete_transaction("tx1")
    assert result.found is True
    assert result.files_deleted == 3
    assert deleted["prefixes"] == ["__transactions__/None/None/tx1/"]

    with Sess() as s:
        assert s.query(Transaction).count() == 0
        assert s.query(Document).count() == 0
        assert s.query(DocumentIndex).count() == 0
        assert s.query(TransactionStep).count() == 0
        assert s.query(Message).count() == 0
        assert s.query(Cost).count() == 0


def test_delete_transaction_idempotent(core_db):
    from alambic_core.services import delete_transaction

    Sess, _ = core_db
    _seed_full_transaction(Sess)
    assert delete_transaction("tx1").found is True
    # Re-supprimer ne lève pas, renvoie found=False.
    second = delete_transaction("tx1")
    assert second.found is False
    assert second.files_deleted == 0


def test_delete_unknown_transaction(core_db):
    from alambic_core.services import delete_transaction

    Sess, deleted = core_db
    result = delete_transaction("does-not-exist")
    assert result.found is False
    # Garage n'est pas touché si la transaction n'existe pas.
    assert deleted["prefixes"] == []


def test_work_prefix_format(core_db):
    from alambic_core.models import Transaction
    from alambic_core.services import transaction_work_prefix

    # Objet non persisté : on teste le format du préfixe, pas la DB.
    tx = Transaction(
        id="trx-abc",
        account_id="acc1",
        config_id="cfg1",
        status="WORKING",
        process="NEWDOC",
    )
    assert transaction_work_prefix(tx) == "__transactions__/acc1/cfg1/trx-abc/"
