"""Tests d'intégration de la tâche de détection multi-document."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from unittest.mock import patch

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


def _payload():
    return {
        "transaction": {"transactionId": "tx1"},
        "document": {"documentId": "doc1", "file": {"bucket": "b", "key": "in/doc1.pdf"}},
        "configId": "cfg1",
        "accountId": "acc1",
    }


def _result(count, docs, cost=0.0):
    from alambic_core.ai.multi_doc_detector import MultiDocResult

    return MultiDocResult(
        count=count, documents=docs, cost=cost, provider="mistral",
        model="pixtral-large-latest", source="vision_vpixtral-large-latest",
    )


def test_multi_doc_disabled_skips():
    """multi_doc_detect désactivé → aucun sous-doc, pas d'appel vision."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Transaction

    from alambic_workers.tasks.multi_doc import detect_multi_doc

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="c", multi_doc_detect=False))
        s.commit()

    out = detect_multi_doc(_payload())
    assert out["children"] == []
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_multi_doc_single_records_cost():
    """Mono-document : pas de sous-doc, mais le coût Pixtral est tracé."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Cost, Transaction

    from alambic_workers.tasks.multi_doc import detect_multi_doc

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="c", multi_doc_detect=True))
        s.commit()

    with (
        patch("alambic_workers.tasks.multi_doc.storage.get_bytes", return_value=b"%PDF-fake"),
        patch("alambic_workers.tasks.multi_doc._first_page_to_png", return_value=b"PNG"),
        patch(
            "alambic_workers.tasks.multi_doc.MultiDocDetector.detect",
            return_value=_result(1, [], cost=0.001),
        ),
    ):
        out = detect_multi_doc(_payload())

    assert out["children"] == []
    assert out["multi_doc"]["detected"] is False
    with Sess() as s:
        costs = s.query(Cost).filter_by(process="DETECT_MULTI_DOC").all()
        assert len(costs) == 1
        assert costs[0].provider == "mistral"
        assert costs[0].source == "vision_vpixtral-large-latest"
        assert float(costs[0].amount) == 0.001
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_multi_doc_creates_subdocs():
    """Multi-document : N sous-docs croppés, parent déprécié, coût tracé."""
    dbfile, Sess, engine = _setup()
    from alambic_core.domain.enums import DocumentStatus
    from alambic_core.models import Config, Cost, Document, Transaction

    from alambic_workers.tasks.multi_doc import detect_multi_doc

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="c", multi_doc_detect=True))
        s.add(
            Document(
                id="doc1", transaction_id="tx1", status="OCR_DONE", process="X",
                bucket_name="b", object_key="in/doc1.pdf",
            )
        )
        s.commit()

    docs = [
        {"type": "Carte Grise", "confidence": 0.9, "bbox": {"x": 2, "y": 2, "w": 45, "h": 35}},
        {"type": "Permis", "confidence": 0.88, "bbox": {"x": 50, "y": 50, "w": 45, "h": 40}},
    ]

    with (
        patch("alambic_workers.tasks.multi_doc.storage.get_bytes", return_value=b"%PDF-fake"),
        patch("alambic_workers.tasks.multi_doc._first_page_to_png", return_value=b"PNG"),
        patch("alambic_workers.tasks.multi_doc._crop_to_pdf", return_value=b"%PDF-crop"),
        patch("alambic_workers.tasks.multi_doc.storage.put_object", return_value=None),
        patch(
            "alambic_workers.tasks.multi_doc.MultiDocDetector.detect",
            return_value=_result(2, docs, cost=0.003),
        ),
    ):
        out = detect_multi_doc(_payload())

    # 2 sous-docs créés.
    assert out["multi_doc"]["detected"] is True
    assert len(out["children"]) == 2
    assert out["children"][0]["source"] == "multi_doc_split"

    with Sess() as s:
        # Sous-docs persistés avec parent_id et statut à ré-OCRiser.
        subs = s.query(Document).filter_by(parent_id="doc1").all()
        assert len(subs) == 2
        assert all(d.status == DocumentStatus.CONVERTED_TO_PDF.value for d in subs)
        # Parent déprécié.
        parent = s.get(Document, "doc1")
        assert parent.status == DocumentStatus.DEPRECATED.value
        # Coût tracé.
        costs = s.query(Cost).filter_by(process="DETECT_MULTI_DOC").all()
        assert len(costs) == 1
        assert float(costs[0].amount) == 0.003
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_multi_doc_subdoc_not_redetected():
    """Un sous-doc (source=multi_doc_split) n'est pas re-détecté (anti-boucle)."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Transaction

    from alambic_workers.tasks.multi_doc import detect_multi_doc

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="c", multi_doc_detect=True))
        s.commit()

    payload = _payload()
    payload["document"]["source"] = "multi_doc_split"
    out = detect_multi_doc(payload)
    assert out["children"] == []
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_render_resolution_is_capped():
    """Le rendu d'une grande page est borné en pixels (anti-OOM)."""
    import io

    import fitz
    from PIL import Image

    from alambic_workers.tasks.multi_doc import (
        _CROP_MAX_PIXELS,
        _DETECT_MAX_PIXELS,
        _first_page_to_png,
        _zoom_for,
    )

    # Grande page ~12 Mpx à zoom 1 (simule un scan haute résolution).
    doc = fitz.open()
    doc.new_page(width=3000, height=4000)
    pdf_bytes = doc.tobytes()
    doc.close()

    detect = Image.open(io.BytesIO(_first_page_to_png(pdf_bytes, max_pixels=_DETECT_MAX_PIXELS)))
    assert detect.width * detect.height <= _DETECT_MAX_PIXELS * 1.05

    crop = Image.open(io.BytesIO(_first_page_to_png(pdf_bytes, max_pixels=_CROP_MAX_PIXELS)))
    assert crop.width * crop.height <= _CROP_MAX_PIXELS * 1.05

    # Une petite page n'est jamais upscalée.
    small = fitz.open()
    small.new_page(width=595, height=842)
    assert _zoom_for(small[0], _CROP_MAX_PIXELS) == 1.0
    small.close()
