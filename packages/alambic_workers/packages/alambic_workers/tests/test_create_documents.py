"""
Tests du portage de create_documents.py sur alambic_core.

Valident la logique métier portée depuis flowerscan_lib : recopie des index du
parent vers l'enfant, ajout des index metadata/extracted, et dépréciation du
parent avec normalisation de l'id suffixé. SQLite en mémoire, sans Docker.
"""

from __future__ import annotations

import alambic_core.models  # noqa: F401
import pytest
from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.models import Account, Document, DocumentIndex, Transaction
from alambic_core.repositories import DocumentIndexRepository
from alambic_core.security.fernet_provider import FernetSecretProvider
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alambic_workers.tasks.create_documents import (
    _deprecate_parent,
    normalize_index,
    update_document,
)


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
def with_parent(session):
    """Un parent doc-parent avec 2 index (1 metadata, 1 extracted)."""
    session.add_all([Account(account_name="A"), Transaction(id="trx-1", status="W")])
    session.commit()
    session.add(Document(id="doc-parent", transaction_id="trx-1", status="WORKING"))
    session.commit()
    session.add_all(
        [
            DocumentIndex(
                document_id="doc-parent",
                index_name="client",
                index_value="ACME",
                index_type="metadata",
            ),
            DocumentIndex(
                document_id="doc-parent",
                index_name="annee",
                index_value="2026",
                index_type="extracted",
            ),
        ]
    )
    session.commit()
    return session


def test_normalize_index_both_conventions():
    assert normalize_index({"name": "a", "value": "1"})["name"] == "a"
    assert normalize_index({"index_name": "b", "index_value": "2"})["name"] == "b"
    assert normalize_index({"index_value": "2"})["value"] == "2"


def test_update_document_creates_child_with_inherited_and_new_indexes(with_parent):
    s = with_parent
    file = {
        "documentId": "doc-child",
        "transactionId": "trx-1",
        "bucket": "alambic-work",
        "key": "child.pdf",
        "datas": [{"name": "facture", "value": "F-001"}],
        "indexes": [{"name": "total", "value": "42", "index_score": "0.9"}],
    }
    result = update_document(s, parent_document_id="doc-parent", file=file)
    s.commit()

    assert result is not None
    assert result["documentId"] == "doc-child"
    child = s.get(Document, "doc-child")
    assert child.status == "OK"
    assert child.bucket_name == "alambic-work"

    idx = DocumentIndexRepository(s).by_document("doc-child")
    pairs = {(i.index_name, i.index_type) for i in idx}
    assert ("client", "metadata") in pairs  # hérité du parent
    assert ("annee", "extracted") in pairs  # hérité du parent
    assert ("facture", "metadata") in pairs  # nouveau metadata
    assert ("total", "extracted") in pairs  # nouveau extracted


def test_update_document_error_returns_none(with_parent):
    s = with_parent
    file = {
        "documentId": "doc-err",
        "transactionId": "trx-1",
        "bucket": "b",
        "key": "k",
        "error_code": "BOOM",
    }
    result = update_document(s, parent_document_id="doc-parent", file=file)
    s.commit()
    assert result is None
    assert s.get(Document, "doc-err").status == "ERROR"


def test_deprecate_parent_with_suffix_normalization(with_parent):
    s = with_parent
    # L'id suffixé _00001 doit être normalisé vers doc-parent
    _deprecate_parent(s, "doc-parent_00001", processed_ids={"doc-child"})
    s.commit()
    assert s.get(Document, "doc-parent").status == "DEPRECATED"


def test_deprecate_parent_skips_if_in_processed(with_parent):
    s = with_parent
    # Si l'id (même normalisé) fait partie des docs créés, on ne déprécie pas
    # le mauvais. Ici doc-parent est dans processed → ni candidat exact ni stripped.
    _deprecate_parent(s, "doc-parent", processed_ids={"doc-parent"})
    s.commit()
    # doc-parent ne doit PAS être déprécié (il vient d'être "créé")
    assert s.get(Document, "doc-parent").status == "WORKING"
