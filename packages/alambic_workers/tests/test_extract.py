"""Test d'intégration de la tâche d'extraction."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress

from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _setup():
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    Base.metadata.create_all(get_engine())
    return dbfile, get_sessionmaker(), get_engine()


def test_extract_mixed_fields(monkeypatch):
    """Extraction d'un doctype mixte : un champ regex + un champ LLM."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Cost, Doctype, Document, DocumentIndex, Transaction

    content = {
        "description": "Facture",
        "fields": [
            {"field_name": "date", "use_ia": 0, "regexp": r"\d{2}/\d{2}/\d{4}", "required": 1},
            {"field_name": "client", "use_ia": 1, "field_description": "le client", "required": 1},
        ],
    }
    import base64

    line = base64.b64encode(b"Date: 15/03/2024").decode()
    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(
            Doctype(
                id="dt1",
                doctype_name="facture",
                json_content=json.dumps(content),
                account_id="acc1",
            )
        )
        s.add(Config(id="cfg1", config_name="C", account_id="acc1", doctype_id="dt1"))
        s.add(
            Document(
                id="doc1",
                transaction_id="tx1",
                status="OCR_DONE",
                process="X",
                ocr_markdown="Facture\nDate: 15/03/2024\nClient: ACME",
                ocr_lines=[
                    {
                        "page": 1,
                        "lines": [
                            {"text": line, "position": {"x0": 0, "y0": 0, "x1": 50, "y1": 5}}
                        ],
                        "barcodes": [],
                    }
                ],
                barcodes=[],
            )
        )
        s.commit()

    # Mocker l'extracteur LLM (pas de réseau).
    import alambic_workers.tasks.extract as ext_mod

    class _FakeExtractor:
        def extract(self, text, doctype_name, doctype_desc, fields):
            return {
                "indexes": {"client": {"value": "ACME", "score": "0.92"}},
                "extraction": {"cost": 0.003, "provider": "mistral", "model": "m"},
            }

    monkeypatch.setattr(ext_mod, "_get_extractor", lambda config: _FakeExtractor())

    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {"documentId": "doc1"},
        "configId": "cfg1",
        "accountId": "acc1",
    }
    ext_mod.extract_document(payload)

    with Sess() as s:
        idx = {
            i.index_name: i
            for i in s.query(DocumentIndex).filter(DocumentIndex.document_id == "doc1").all()
        }
        assert idx["date"].index_value == "15/03/2024"
        assert idx["date"].index_score == "1.0"
        assert idx["client"].index_value == "ACME"
        doc = s.get(Document, "doc1")
        assert doc.extraction_summary["extraction_ok"] is True
        assert doc.status == "PENDING_VALIDATION"
        costs = s.query(Cost).filter(Cost.document_id == "doc1", Cost.process == "EXTRACT").all()
        assert len(costs) == 1

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_extract_skips_without_doctype(monkeypatch):
    """Sans doctype (donc sans champs), l'extraction est sautée proprement."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Document, Transaction

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="C", account_id="acc1"))  # pas de doctype_id
        s.add(
            Document(
                id="doc1", transaction_id="tx1", status="OCR_DONE", process="X", ocr_markdown="x"
            )
        )
        s.commit()

    import alambic_workers.tasks.extract as ext_mod

    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {"documentId": "doc1"},
        "configId": "cfg1",
        "accountId": "acc1",
    }
    result = ext_mod.extract_document(payload)
    assert result["extraction"]["skipped"] == "no_fields"

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)
