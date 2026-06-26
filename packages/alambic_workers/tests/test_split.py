"""Tests de l'étape de découpage (brique F)."""

from __future__ import annotations

import base64
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


def _b64(t):
    return base64.b64encode(t.encode()).decode()


def _make_pdf(n_pages):
    pdf = tempfile.mktemp(suffix=".pdf")
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page contenu {i + 1}", fontsize=11)
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

    # 3 pages OCR avec barcodes : DOC_A (p1,p2), DOC_B (p3) → coupe en page 3.
    ocr_pages = [
        {
            "page": 1,
            "lines": [
                {"text": _b64("contenu 1"), "position": {"x0": 10, "y0": 10, "x1": 50, "y1": 13}}
            ],
            "barcodes": [
                {
                    "value": "DOC_A",
                    "format": "Code128",
                    "position": {"x0": 70, "y0": 5, "x1": 95, "y1": 10},
                }
            ],
        },
        {
            "page": 2,
            "lines": [
                {"text": _b64("contenu 2"), "position": {"x0": 10, "y0": 10, "x1": 50, "y1": 13}}
            ],
            "barcodes": [
                {
                    "value": "DOC_A",
                    "format": "Code128",
                    "position": {"x0": 70, "y0": 5, "x1": 95, "y1": 10},
                }
            ],
        },
        {
            "page": 3,
            "lines": [
                {"text": _b64("contenu 3"), "position": {"x0": 10, "y0": 10, "x1": 50, "y1": 13}}
            ],
            "barcodes": [
                {
                    "value": "DOC_B",
                    "format": "Code128",
                    "position": {"x0": 70, "y0": 5, "x1": 95, "y1": 10},
                }
            ],
        },
    ]

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
                ocr_lines=ocr_pages,
            )
        )
        s.add(Config(id="cfg1", config_name="c", edenai_settings={}))
        s.commit()

    yield Sess
    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _payload():
    return {
        "transaction": {"transactionId": "tx1"},
        "configId": "cfg1",
        "document": {
            "documentId": "d1",
            "file": {"bucket": "alambic-work", "key": "__transactions__/a/c/tx1/d1.pdf"},
        },
    }


def test_split_creates_children(core_db, monkeypatch):
    """Document 3 pages, barcode change en p3 → 2 enfants, parent déprécié."""
    import shutil

    import alambic_workers.tasks.split as split_task

    pdf = _make_pdf(3)
    monkeypatch.setattr(
        split_task.storage, "download_to", lambda b, k, d: (shutil.copy(pdf, d), d)[1]
    )
    monkeypatch.setattr(split_task.storage, "put_object", lambda b, k, p: None)

    from alambic_core.domain.enums import DocumentStatus
    from alambic_core.models import Document

    payload = split_task.split_document(_payload())
    children = payload["children"]
    assert len(children) == 2  # [1,2] et [3]
    assert children[0]["pages"] == [1, 2]
    assert children[1]["pages"] == [3]

    with core_db() as s:
        # Parent déprécié.
        parent = s.get(Document, "d1")
        assert parent.status == DocumentStatus.DEPRECATED.value
        # Enfants créés avec parent_id.
        c1 = s.get(Document, "d1_split_00001")
        c2 = s.get(Document, "d1_split_00002")
        assert c1 is not None and c1.parent_id == "d1"
        assert c2 is not None and c2.parent_id == "d1"


def test_no_split_single_document(core_db, monkeypatch):
    """Document homogène (même barcode partout) → pas de découpage."""
    from alambic_core.db.session import session_scope
    from alambic_core.models import Document

    import alambic_workers.tasks.split as split_task

    # Uniformise les barcodes (tous DOC_A) → un seul document logique.
    with session_scope() as s:
        d = s.get(Document, "d1")
        pages = d.ocr_lines
        for p in pages:
            p["barcodes"] = [
                {
                    "value": "DOC_A",
                    "format": "Code128",
                    "position": {"x0": 70, "y0": 5, "x1": 95, "y1": 10},
                }
            ]
        d.ocr_lines = pages
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(d, "ocr_lines")

    payload = split_task.split_document(_payload())
    assert payload["children"] == []
