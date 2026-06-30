"""Tests de l'étape readCAB (gating + lecture + persistance)."""

from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import suppress

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _make_barcode_pdf(value="SEP12345"):
    import fitz
    from barcode import Code128
    from barcode.writer import ImageWriter
    from PIL import Image

    buf = io.BytesIO()
    Code128(value, writer=ImageWriter()).write(buf)
    buf.seek(0)
    pdf_path = tempfile.mktemp(suffix=".pdf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    img_path = tempfile.mktemp(suffix=".png")
    Image.open(buf).convert("RGB").save(img_path)
    page.insert_image(fitz.Rect(100, 100, 495, 250), filename=img_path)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


@pytest.fixture
def core_db():
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Config, Doctype, Document, Transaction

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
        # Doctype avec un bcr_type → CAB activé.
        dt = Doctype(
            id="dt1",
            doctype_name="recommande",
            json_content=json.dumps({"fields": [{"field_name": "no", "bcr_type": "Code128"}]}),
        )
        s.add(dt)
        s.add(Config(id="cfg1", config_name="c", doctype_id="dt1"))
        s.commit()

    yield Sess
    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _payload(config_id="cfg1"):
    return {
        "transaction": {"transactionId": "tx1"},
        "configId": config_id,
        "document": {
            "documentId": "d1",
            "file": {"bucket": "alambic-work", "key": "__transactions__/a/c/tx1/d1.pdf"},
        },
    }


def test_read_cab_reads_and_persists(core_db, monkeypatch):
    """Doctype avec bcr_type : lit le code-barres et le persiste sur le document."""
    import shutil

    import alambic_workers.tasks.barcode as cab

    pdf = _make_barcode_pdf("SEP12345")
    monkeypatch.setattr(cab.storage, "download_to", lambda b, k, d: (shutil.copy(pdf, d), d)[1])

    from alambic_core.models import Document

    payload = cab.read_cab_document(_payload())
    assert payload["barcodes"], "aucun code-barres lu"
    assert payload["barcodes"][0]["value"] == "SEP12345"

    with core_db() as s:
        d = s.get(Document, "d1")
        assert d.barcodes[0]["value"] == "SEP12345"


def test_read_cab_skipped_without_bcr_type(core_db, monkeypatch):
    """Doctype sans bcr_type : CAB sauté, pas de téléchargement ni lecture."""
    from alambic_core.db.session import session_scope
    from alambic_core.models import Doctype

    import alambic_workers.tasks.barcode as cab

    # Retire le bcr_type du doctype.
    with session_scope() as s:
        dt = s.get(Doctype, "dt1")
        dt.json_content = json.dumps({"fields": [{"field_name": "x", "bcr_type": ""}]})

    called = {"download": False}

    def _no_download(*a, **k):
        called["download"] = True
        raise AssertionError("ne doit pas télécharger")

    monkeypatch.setattr(cab.storage, "download_to", _no_download)

    payload = cab.read_cab_document(_payload())
    assert payload["barcodes"] == []
    assert called["download"] is False
