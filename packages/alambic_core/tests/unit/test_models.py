"""Tests unitaires des 8 modèles (sur SQLite en mémoire)."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from alambic_core.models import (
    Account,
    Config,
    Cost,
    Doctype,
    Document,
    DocumentIndex,
    Message,
    Transaction,
)


def test_all_tables_created(engine):
    from alambic_core.db.base import Base

    expected = {
        "accounts",
        "configs",
        "doctypes",
        "transactions",
        "documents",
        "document_indexes",
        "messages",
        "costs",
    }
    assert expected <= set(Base.metadata.tables.keys())


def test_full_hierarchy_insert(session):
    acc = Account(account_name="ACME", edenai_secret_key="sk-123")
    cfg = Config(config_name="flux1", account=acc, edenai_secret_enc="sk-edenai")
    dt = Doctype(doctype_name="facture", account_id=acc.id)
    session.add_all([acc, cfg, dt])
    session.flush()

    tx = Transaction(status="WORKING", account_id=acc.id, config_id=cfg.id)
    session.add(tx)
    session.flush()

    doc = Document(transaction_id=tx.id, status="CREATED", doctype_id=dt.id)
    doc.indexes.append(
        DocumentIndex(index_type="metadata", index_name="client", index_value="ACME")
    )
    session.add(doc)
    session.add(Cost(amount=0.0042, document_id=doc.id, transaction_id=tx.id, provider="openai"))
    session.add(Message(transaction_id=tx.id, level="INFO", source="ingestion", text="ok"))
    session.commit()

    assert doc.transaction.account.account_name == "ACME"
    assert len(doc.indexes) == 1


def test_secrets_encrypted_at_rest(session, engine):
    acc = Account(account_name="X", edenai_secret_key="super-secret")
    session.add(acc)
    session.commit()
    with engine.connect() as c:
        raw = c.execute(text("SELECT edenai_secret_key FROM accounts")).scalar()
    assert raw != "super-secret"
    assert raw.startswith("gAAAA")  # chiffré


def test_secret_decrypted_for_code(session):
    acc = Account(account_name="X", edenai_secret_key="lisible")
    session.add(acc)
    session.commit()
    fetched = session.scalars(select(Account)).first()
    assert fetched.edenai_secret_key == "lisible"


def test_document_index_query_by_type(session):
    acc = Account(account_name="X")
    tx = Transaction(status="W", account=acc)
    doc = Document(transaction=tx, status="CREATED")
    doc.indexes.append(DocumentIndex(index_type="metadata", index_name="a", index_value="1"))
    doc.indexes.append(DocumentIndex(index_type="extracted", index_name="b", index_value="2"))
    session.add_all([acc, tx, doc])
    session.commit()

    metas = session.scalars(
        select(DocumentIndex).where(
            DocumentIndex.document_id == doc.id,
            DocumentIndex.index_type == "metadata",
        )
    ).all()
    assert len(metas) == 1
    assert metas[0].index_name == "a"


def test_document_optimistic_locking(engine):
    acc = Account(account_name="X")
    tx = Transaction(status="W", account=acc)
    doc = Document(transaction=tx, status="CREATED")
    with Session(engine) as s:
        s.add_all([acc, tx, doc])
        s.commit()
        doc_id = doc.id

    with Session(engine) as s1, Session(engine) as s2:
        d1 = s1.get(Document, doc_id)
        d2 = s2.get(Document, doc_id)
        d1.status = "OCR_DONE"
        s1.commit()
        d2.status = "FAILED"
        with pytest.raises(StaleDataError):
            s2.commit()


def test_transaction_optimistic_locking(engine):
    acc = Account(account_name="X")
    tx = Transaction(status="W", account=acc)
    with Session(engine) as s:
        s.add_all([acc, tx])
        s.commit()
        tx_id = tx.id

    with Session(engine) as s1, Session(engine) as s2:
        t1 = s1.get(Transaction, tx_id)
        t2 = s2.get(Transaction, tx_id)
        t1.status = "COMPLETED"
        s1.commit()
        t2.status = "ERROR"
        with pytest.raises(StaleDataError):
            s2.commit()


def test_message_requires_exactly_one_parent(session):
    """La CheckConstraint impose tx XOR doc. Un message sans parent est rejeté."""
    session.add(Message(level="INFO", source="x", text="orphelin"))
    with pytest.raises(Exception):
        session.commit()
    session.rollback()
