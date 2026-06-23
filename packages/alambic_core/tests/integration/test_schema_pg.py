"""
Tests d'INTÉGRATION sur vrai PostgreSQL — valident ce que SQLite ne peut pas.

Tous marqués @pytest.mark.integration (nécessitent Docker).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from alambic_core.models import (
    Account,
    Document,
    Message,
    Transaction,
)

pytestmark = pytest.mark.integration


def test_schema_creates_on_real_postgres(pg_engine):
    """Les 8 tables se créent sur un vrai Postgres (JSONB, contraintes incluses)."""
    with pg_engine.connect() as c:
        rows = c.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        ).fetchall()
    tables = {r[0] for r in rows}
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
    assert expected <= tables


def test_jsonb_is_real_jsonb(pg_engine):
    """extraction_summary est un vrai JSONB (pas du texte) et interrogeable par contenu."""
    with Session(pg_engine) as s:
        acc = Account(account_name="X")
        tx = Transaction(status="CREATED", account=acc)
        doc = Document(
            transaction=tx,
            status="CREATED",
            extraction_summary={"source": "ai", "extraction_ok": True, "score": 0.9},
        )
        s.add_all([acc, tx, doc])
        s.commit()
        doc_id = doc.id

    # Requête JSONB native @> (contient) — impossible en SQLite
    with pg_engine.connect() as c:
        found = c.execute(
            text('SELECT id FROM documents WHERE extraction_summary @> \'{"source": "ai"}\'')
        ).fetchall()
    assert any(r[0] == doc_id for r in found)


def test_gin_index_exists(pg_engine):
    """L'index GIN sur extraction_summary est bien présent et de type GIN."""
    with pg_engine.connect() as c:
        rows = c.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='documents'")
        ).fetchall()
    by_name = {r[0]: r[1] for r in rows}
    assert "ix_documents_extraction_summary_gin" in by_name, by_name
    # indexdef contient "USING gin" pour un vrai index GIN
    assert "gin" in by_name["ix_documents_extraction_summary_gin"].lower()


def test_ondelete_cascade_documents(pg_engine):
    """Supprimer une transaction supprime ses documents (ondelete CASCADE réel)."""
    with Session(pg_engine) as s:
        acc = Account(account_name="X")
        tx = Transaction(status="CREATED", account=acc)
        doc = Document(transaction=tx, status="CREATED")
        s.add_all([acc, tx, doc])
        s.commit()
        tx_id, doc_id = tx.id, doc.id

        s.delete(s.get(Transaction, tx_id))
        s.commit()
        # Le document doit avoir disparu en cascade
        assert s.get(Document, doc_id) is None


def test_ondelete_setnull_doctype(pg_engine):
    """Supprimer un doctype met doctype_id à NULL sur les documents (SET NULL)."""
    from alambic_core.models import Doctype

    with Session(pg_engine) as s:
        acc = Account(account_name="X")
        dt = Doctype(doctype_name="facture")
        tx = Transaction(status="CREATED", account=acc)
        s.add_all([acc, dt, tx])
        s.flush()
        doc = Document(transaction=tx, status="CREATED", doctype_id=dt.id)
        s.add(doc)
        s.commit()
        doc_id, dt_id = doc.id, dt.id

        s.delete(s.get(Doctype, dt_id))
        s.commit()
        refreshed = s.get(Document, doc_id)
        assert refreshed is not None  # le doc survit
        assert refreshed.doctype_id is None  # mais la FK est nullifiée


def test_check_constraint_message_enforced(pg_engine):
    """La CheckConstraint (exactement un parent : tx XOR doc) est appliquée par PG.

    On teste le cas ZÉRO parent : ni transaction_id ni document_id. C'est plus
    propre que deux parents, car deux FK valides demanderaient de créer un vrai
    document, et une FK bidon déclencherait une erreur de clé étrangère AVANT
    la CheckConstraint (test faussement vert).
    """
    with Session(pg_engine) as s:
        s.add(Message(level="INFO", text="orphelin sans parent"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_optimistic_locking_real_pg(pg_engine):
    """Le versioning optimiste lève StaleDataError sur conflit concurrent réel."""
    with Session(pg_engine) as s:
        acc = Account(account_name="X")
        tx = Transaction(status="CREATED", account=acc)
        s.add_all([acc, tx])
        s.commit()
        tx_id = tx.id

    with Session(pg_engine) as s1, Session(pg_engine) as s2:
        t1 = s1.get(Transaction, tx_id)
        t2 = s2.get(Transaction, tx_id)
        t1.status = "VALIDATED"
        s1.commit()
        t2.status = "FAILED"
        with pytest.raises(StaleDataError):
            s2.commit()


def test_secret_encrypted_in_real_pg(pg_engine):
    """Les secrets sont chiffrés au repos dans le vrai Postgres."""
    with Session(pg_engine) as s:
        acc = Account(account_name="X", edenai_secret_key="vrai-secret")
        s.add(acc)
        s.commit()

    with pg_engine.connect() as c:
        raw = c.execute(text("SELECT edenai_secret_key FROM accounts")).scalar()
    assert raw != "vrai-secret"
    assert raw.startswith("gAAAA")  # token Fernet
