"""Tests de l'étape OCR (extraction hybride + persistance + coût)."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress

import fitz
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _native_pdf():
    pdf = tempfile.mktemp(suffix=".pdf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Mise en demeure bancaire, contrat 12345.", fontsize=11)
    doc.save(pdf)
    doc.close()
    return pdf


@pytest.fixture
def core_db():
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Config, Document, Transaction

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()
    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X"))
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="C",
                process="X",
                bucket_name="alambic-work",
                object_key="__transactions__/a/c/tx1/d1.pdf",
            )
        )
        s.add(
            Config(
                id="cfg1",
                config_name="c",
                edenai_settings={
                    "ocr_provider": "ocr/mistral",
                    "ocr_end_point": "https://ed/ocr",
                    "ocr_language": "fr",
                },
            )
        )
        s.commit()

    yield Sess
    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _payload():
    return {
        "transaction": {"transactionId": "tx1"},
        "configId": "cfg1",
        "accountId": "acc",
        "document": {
            "documentId": "d1",
            "file": {"bucket": "alambic-work", "key": "__transactions__/a/c/tx1/d1.pdf"},
        },
        "barcodes": [
            {"value": "SEP12345", "page": 1, "position": {"x0": 1, "y0": 1, "x1": 2, "y1": 2}}
        ],
    }


def test_read_ocr_persists_markdown_and_cost(core_db, monkeypatch):
    import shutil

    import alambic_workers.tasks.ocr as ocr_task

    pdf = _native_pdf()
    monkeypatch.setattr(
        ocr_task.storage, "download_to", lambda b, k, d: (shutil.copy(pdf, d), d)[1]
    )

    # On simule le client OCR (pas d'appel EdenAI réel) : il renverra du coût si
    # appelé (zones hybrides), mais le PDF natif peut suffire.
    from alambic_core.ai.edenai_ocr import OcrResult

    def _fake_ocr_init(self, conf):
        self.conf = conf

    monkeypatch.setattr(ocr_task.DocumentOcr, "__init__", _fake_ocr_init)
    monkeypatch.setattr(
        ocr_task.DocumentOcr,
        "ocr_bytes",
        lambda self, data, fn: OcrResult(text="zone", provider="mistral", cost=0.01),
        raising=False,
    )

    from alambic_core.models import Cost, Document

    payload = ocr_task.read_ocr_document(_payload())
    assert "ocr" in payload

    with core_db() as s:
        d = s.get(Document, "d1")
        # Le markdown contient le texte natif + le barcode injecté.
        assert "[PAGE 1]" in d.ocr_markdown
        assert "Mise en demeure" in d.ocr_markdown
        assert "SEP12345" in d.ocr_markdown
        assert isinstance(d.ocr_lines, list)
        # Si du coût a été généré (zones OCR), une ligne Cost existe.
        costs = s.query(Cost).filter_by(transaction_id="tx1").all()
        for c in costs:
            assert c.process == "OCR"
            assert c.document_id == "d1"


def test_read_ocr_skips_without_config(core_db, monkeypatch):
    import alambic_workers.tasks.ocr as ocr_task

    payload = _payload()
    payload["configId"] = "inexistant"
    out = ocr_task.read_ocr_document(payload)
    assert out["ocr"].get("skipped") == "no_config"
