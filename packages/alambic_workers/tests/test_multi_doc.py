"""Tests d'intégration de la tâche multi-document (segmentation OpenCV locale)."""

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


def _seg(count, docs):
    from alambic_core.vision import SegmentResult

    return SegmentResult(count=count, documents=docs, method="opencv_v1")


def test_multi_doc_disabled_skips():
    """multi_doc_detect désactivé → aucun sous-doc, pas de segmentation."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Transaction

    from alambic_workers.tasks.multi_doc import detect_multi_doc

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="c", multi_doc_detect=False))
        s.commit()

    with patch("alambic_workers.tasks.multi_doc.segment_from_png_bytes") as seg:
        out = detect_multi_doc(_payload())
    assert out["children"] == []
    seg.assert_not_called()
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_multi_doc_single_no_children():
    """Mono-document : pas de sous-doc, aucun coût (traitement local gratuit)."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Config, Cost, Transaction

    from alambic_workers.tasks.multi_doc import detect_multi_doc

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X", config_id="cfg1", account_id="acc1"))
        s.add(Config(id="cfg1", config_name="c", multi_doc_detect=True))
        s.commit()

    with (
        patch("alambic_workers.tasks.multi_doc.storage.get_bytes", return_value=b"%PDF"),
        patch("alambic_workers.tasks.multi_doc._first_page_to_png", return_value=b"PNG"),
        patch(
            "alambic_workers.tasks.multi_doc.segment_from_png_bytes",
            return_value=_seg(1, []),
        ),
    ):
        out = detect_multi_doc(_payload())

    assert out["children"] == []
    assert out["multi_doc"]["detected"] is False
    assert out["multi_doc"]["method"] == "opencv_v1"
    with Sess() as s:
        assert s.query(Cost).filter_by(process="DETECT_MULTI_DOC").count() == 0
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_multi_doc_creates_subdocs():
    """Multi-document : N sous-docs croppés, parent déprécié, aucun coût."""
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
        {"bbox": {"x": 2, "y": 2, "w": 45, "h": 35}},
        {"bbox": {"x": 50, "y": 50, "w": 45, "h": 40}},
    ]

    with (
        patch("alambic_workers.tasks.multi_doc.storage.get_bytes", return_value=b"%PDF"),
        patch("alambic_workers.tasks.multi_doc._first_page_to_png", return_value=b"PNG"),
        patch("alambic_workers.tasks.multi_doc._crop_to_pdf", return_value=b"%PDF-crop"),
        patch("alambic_workers.tasks.multi_doc.storage.put_object", return_value=None),
        patch(
            "alambic_workers.tasks.multi_doc.segment_from_png_bytes",
            return_value=_seg(2, docs),
        ),
    ):
        out = detect_multi_doc(_payload())

    assert out["multi_doc"]["detected"] is True
    assert len(out["children"]) == 2
    assert out["children"][0]["source"] == "multi_doc_split"

    with Sess() as s:
        subs = s.query(Document).filter_by(parent_id="doc1").all()
        assert len(subs) == 2
        assert all(d.status == DocumentStatus.CONVERTED_TO_PDF.value for d in subs)
        # Le sous-doc doit repartir AVANT l'OCR (sinon le step OCR le saute et le
        # doc arrive vide à la classification). process=FILE_CONVERTED garantit
        # que read_ocr s'exécutera bien sur le crop.
        assert all(d.process == "FILE_CONVERTED" for d in subs)
        parent = s.get(Document, "doc1")
        assert parent.status == DocumentStatus.DEPRECATED.value
        assert s.query(Cost).filter_by(process="DETECT_MULTI_DOC").count() == 0
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
    with patch("alambic_workers.tasks.multi_doc.segment_from_png_bytes") as seg:
        out = detect_multi_doc(payload)
    assert out["children"] == []
    seg.assert_not_called()
    engine.dispose()
    with suppress(OSError):
        os.unlink(dbfile)


def test_zoom_floor_upscales_small_pages():
    """Un PDF défini en points (petit à zoom 1) est agrandi jusqu'au plancher,
    sinon la segmentation manque de résolution."""
    import fitz

    from alambic_workers.tasks.multi_doc import _zoom_for

    # Page A4 en points (595x841 ≈ 0.5 Mpx à zoom 1) → doit être agrandie.
    doc = fitz.open()
    doc.new_page(width=595, height=841)
    z = _zoom_for(doc[0], max_pixels=8_000_000, min_pixels=2_000_000)
    doc.close()
    assert z > 1.0  # agrandissement
    assert 595 * 841 * z * z >= 1_900_000  # atteint ~le plancher

    # Grande page déjà au-dessus du plafond → réduite.
    doc = fitz.open()
    doc.new_page(width=4000, height=5000)
    z2 = _zoom_for(doc[0], max_pixels=8_000_000, min_pixels=2_000_000)
    doc.close()
    assert z2 < 1.0
    assert 4000 * 5000 * z2 * z2 <= 8_100_000
