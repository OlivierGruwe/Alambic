"""Tests unitaires des repositories (sur SQLite en mémoire)."""

from __future__ import annotations

import pytest

from alambic_core.models import Account, Document, DocumentIndex, Transaction
from alambic_core.repositories import (
    AccountRepository,
    DocumentIndexRepository,
    DocumentRepository,
    MessageRepository,
    TransactionRepository,
)


@pytest.fixture
def sample(session):
    """Un jeu de données minimal : 1 compte, 1 transaction, 2 documents."""
    acc = Account(account_name="ACME")
    tx = Transaction(status="WORKING", account=acc)
    d1 = Document(transaction=tx, status="CREATED")
    d2 = Document(transaction=tx, status="OCR_DONE")
    session.add_all([acc, tx, d1, d2])
    session.commit()
    return {"account": acc, "tx": tx, "d1": d1, "d2": d2}


def test_base_get(session, sample):
    repo = AccountRepository(session)
    fetched = repo.get(sample["account"].id)
    assert fetched is not None
    assert fetched.account_name == "ACME"


def test_base_get_missing_returns_none(session):
    assert AccountRepository(session).get("inexistant") is None


def test_base_count(session, sample):
    assert DocumentRepository(session).count() == 2


def test_base_add(session):
    repo = AccountRepository(session)
    acc = repo.add(Account(account_name="New"))
    assert acc.id is not None
    assert repo.get(acc.id).account_name == "New"


def test_base_delete(session, sample):
    repo = DocumentRepository(session)
    repo.delete(sample["d1"])
    assert repo.count() == 1


def test_document_by_transaction(session, sample):
    docs = DocumentRepository(session).by_transaction(sample["tx"].id)
    assert len(docs) == 2


def test_document_by_status(session, sample):
    docs = DocumentRepository(session).by_status("OCR_DONE")
    assert len(docs) == 1
    assert docs[0].id == sample["d2"].id


def test_transaction_by_status(session, sample):
    txs = TransactionRepository(session).by_status("WORKING")
    assert len(txs) == 1


def test_index_metadata_vs_extracted(session, sample):
    doc = sample["d1"]
    doc.indexes.append(
        DocumentIndex(index_type="metadata", index_name="client", index_value="ACME")
    )
    doc.indexes.append(DocumentIndex(index_type="extracted", index_name="total", index_value="42"))
    session.commit()

    repo = DocumentIndexRepository(session)
    assert len(repo.by_document(doc.id)) == 2
    metas = repo.metadata_of(doc.id)
    assert len(metas) == 1 and metas[0].index_name == "client"
    extracted = repo.extracted_of(doc.id)
    assert len(extracted) == 1 and extracted[0].index_name == "total"


def test_message_for_transaction_and_document(session, sample):
    repo = MessageRepository(session)
    repo.add_for_transaction(sample["tx"].id, "tx message", source="ingestion")
    repo.add_for_document(sample["d1"].id, "doc message", level="WARN")
    session.commit()

    tx_msgs = repo.for_transaction(sample["tx"].id)
    doc_msgs = repo.for_document(sample["d1"].id)
    assert len(tx_msgs) == 1 and tx_msgs[0].text == "tx message"
    assert len(doc_msgs) == 1 and doc_msgs[0].level == "WARN"
