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
        s.add(Config(id="cfg1", config_name="C", account_id="acc1", expected_doctypes=[{"doctype_id": "dt1", "required": True}]))
        s.add(
            Document(
                id="doc1",
                transaction_id="tx1",
                status="OCR_DONE",
                process="X",
                doctype="facture",
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


def test_extract_uses_payload_fields_from_let_it_guess(monkeypatch):
    """En let_it_guess, les champs voyagent par le payload : l'extraction les
    utilise même si le doctype deviné n'est pas en base (ni dans le périmètre)."""
    dbfile, Sess, engine = _setup()
    import base64

    from alambic_core.models import Config, Document, DocumentIndex, Transaction

    line = base64.b64encode(b"Devis N AB123").decode()
    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        # Config SANS doctype 'devis' attendu (let_it_guess a deviné le type).
        s.add(Config(id="cfg1", config_name="C", account_id="acc1"))
        # Aucun Doctype 'devis' en base : il a été deviné librement.
        s.add(
            Document(
                id="doc1", transaction_id="tx1", status="OCR_DONE", process="X",
                doctype="devis", doctype_desc="Devis commercial",
                ocr_markdown="Devis N AB123\nMontant 500",
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

    import alambic_workers.tasks.extract as ext_mod

    # Payload façon classification let_it_guess : les champs sont là.
    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {"documentId": "doc1"},
        "configId": "cfg1",
        "accountId": "acc1",
        "classification": {"type": "devis", "source": "llm_vbootstrap", "confidence": 0.98},
        "fields": [
            {"field_name": "numero_devis", "use_ia": 0,
             "regexp": r"Devis N\s*([A-Z0-9]+)", "required": 1},
        ],
    }
    result = ext_mod.extract_document(payload)

    # L'extraction ne doit PAS être sautée : les champs viennent du payload.
    assert result.get("extraction", {}).get("skipped") != "no_fields"
    with Sess() as s:
        idx = {
            i.index_name: i
            for i in s.query(DocumentIndex).filter(DocumentIndex.document_id == "doc1").all()
        }
        assert "numero_devis" in idx
        # Le champ EST extrait (le point clé : plus d'« extraction sautée »).
        assert "AB123" in idx["numero_devis"].index_value

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_base_doctype_fields_take_priority_over_payload(monkeypatch):
    """Si le doctype existe EN BASE, ses champs (et méthodes d'extraction)
    priment : le payload["fields"] ne doit PAS les écraser."""
    import base64
    import json

    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Doctype, Document, DocumentIndex, Transaction

    # En base : regex qui capture FAC-<num>.
    base_fields = {"fields": [{"field_name": "num", "regexp": r"FAC-(\d+)", "use_ia": 0}],
                   "description": "Facture"}
    line = base64.b64encode(b"Reference FAC-123 du client").decode()
    with Sess() as s:
        cfg = Config(id="cfg1", config_name="auto", account_id="acc1")
        cfg.expected_doctypes = [{"doctype_id": "dt1", "required": True}]
        s.add(cfg)
        s.add(Doctype(id="dt1", doctype_name="facture", account_id="acc1", is_public=False,
                      json_content=json.dumps(base_fields)))
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Document(id="doc1", transaction_id="tx1", doctype="facture", status="OCR_DONE",
                       ocr_lines=[{"page": 1, "lines": [
                           {"text": line, "position": {"x0": 0, "y0": 0, "x1": 9, "y1": 9}}
                       ], "barcodes": []}], barcodes=[]))
        s.commit()

    import alambic_workers.tasks.extract as ext_mod

    # Le payload propose une regex DIFFÉRENTE (XXX-) qui ne doit PAS être utilisée.
    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {"documentId": "doc1"},
        "configId": "cfg1", "accountId": "acc1",
        "classification": {"type": "facture", "source": "lexical", "confidence": 0.99},
        "fields": [{"field_name": "num", "regexp": r"XXX-(\d+)", "use_ia": 0}],
    }
    ext_mod.extract_document(payload)

    with Sess() as s:
        vals = {
            i.index_name: i.index_value
            for i in s.query(DocumentIndex).filter(DocumentIndex.document_id == "doc1").all()
        }
        # La regex de la BASE (FAC-123) a servi, pas celle du payload (XXX-).
        assert any("123" in v for v in vals.values())

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)
