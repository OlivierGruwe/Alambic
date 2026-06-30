"""
Tests de create_transaction (tasks/ingestion.py) — raccord du transaction_key.

Valident que la transaction créée porte bien le transaction_key posé par
start_ingestion, ce qui rend l'idempotence effective (by_transaction_key la
retrouve). SQLite en mémoire, sans Docker.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import alambic_core.models  # noqa: F401
import pytest
from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.models import Account, Config, Transaction
from alambic_core.repositories import TransactionRepository
from alambic_core.security.fernet_provider import FernetSecretProvider
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import alambic_workers.tasks.ingestion as ing


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
    Sess = sessionmaker(bind=eng)
    with Sess() as s:
        s.add(Account(id="Acc_1", account_name="A"))
        s.add(Config(id="Con_1", account_id="Acc_1", config_name="c"))
        s.commit()

    @contextmanager
    def scope():
        s = Sess()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return Sess, scope


def test_create_transaction_persists_key_and_origin(sessions):
    Sess, scope = sessions
    payload = {
        "accountId": "Acc_1",
        "configId": "Con_1",
        "transaction": {"transactionId": "trx-abc", "transaction_key": "KEY_123"},
        "datas": [{"name": "origin", "value": "UI_IMPORT"}],
        "documents": [{"documentId": "doc-abc"}],
    }
    with patch.object(ing, "session_scope", scope):
        out = ing.create_transaction(payload)

    assert out["transaction"]["transactionId"] == "trx-abc"
    with Sess() as s:
        tx = s.get(Transaction, "trx-abc")
        assert tx.transaction_key == "KEY_123"
        assert tx.origin == "UI_IMPORT"
        assert tx.nb_docs == 1


def test_idempotence_loop_closed(sessions):
    """La clé persistée est retrouvable par by_transaction_key (idempotence)."""
    Sess, scope = sessions
    with patch.object(ing, "session_scope", scope):
        ing.create_transaction(
            {
                "accountId": "Acc_1",
                "configId": "Con_1",
                "transaction": {"transactionId": "trx-x", "transaction_key": "DETERM_KEY"},
                "datas": [],
                "documents": [],
            }
        )
    with Sess() as s:
        found = TransactionRepository(s).by_transaction_key("DETERM_KEY")
        assert found is not None
        assert found.id == "trx-x"


def test_create_transaction_replay_no_duplicate(sessions):
    Sess, scope = sessions
    args = {
        "accountId": "Acc_1",
        "configId": "Con_1",
        "transaction": {"transactionId": "trx-dup", "transaction_key": "K"},
        "datas": [],
        "documents": [],
    }
    with patch.object(ing, "session_scope", scope):
        ing.create_transaction(dict(args))
        ing.create_transaction(dict(args))
    with Sess() as s:
        txs = s.query(Transaction).filter(Transaction.id == "trx-dup").all()
        assert len(txs) == 1
