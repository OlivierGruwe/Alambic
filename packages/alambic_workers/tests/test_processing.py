"""Tests de la chaîne de traitement par document (une tâche par étape)."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
def core_db(monkeypatch):
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Document, Transaction

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()
    with Sess() as s:
        s.add(Transaction(id="tx1", status="WORKING", process="DOC_EXTRACTED"))
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="CREATED",
                process="DOC_EXTRACTED",
                bucket_name="alambic-work",
                object_key="__transactions__/a/c/tx1/d1.txt",
            )
        )
        s.commit()

    src = tempfile.mktemp(suffix=".txt")
    Path(src).write_text("Permis AM Marilou.\n" * 5, encoding="utf-8")

    import alambic_workers.tasks.conversion as conv

    monkeypatch.setattr(conv.storage, "download_to", lambda b, k, d: (shutil.copy(src, d), d)[1])
    monkeypatch.setattr(conv.storage, "put_object", lambda b, k, body, metadata=None: None)

    yield Sess
    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _payload(key="d.pdf"):
    return {
        "transaction": {"transactionId": "tx1"},
        "document": {"documentId": "d1", "file": {"key": key}},
    }


def test_normalize_payload():
    from alambic_workers.orchestration.processing import _normalize_payload

    p = _normalize_payload({"transactionId": "tx9"})
    assert p["transaction"]["transactionId"] == "tx9"


def test_convert_queue_routing():
    from alambic_workers.orchestration.processing import _convert_queue

    assert _convert_queue(_payload("d.xlsx")) == "office"
    assert _convert_queue(_payload("d.docx")) == "office"
    assert _convert_queue(_payload("d.pdf")) == "normal"
    assert _convert_queue(_payload("d.txt")) == "normal"


def test_run_processing_launches_convert():
    from alambic_workers.orchestration import processing as P

    routes = []
    with patch.object(
        P.convert, "apply_async", side_effect=lambda args, queue: routes.append(queue)
    ):
        P.run_processing.run(_payload("d.pdf"))
    assert routes == ["normal"]


def test_run_processing_office_to_office_queue():
    from alambic_workers.orchestration import processing as P

    routes = []
    with patch.object(
        P.convert, "apply_async", side_effect=lambda args, queue: routes.append(queue)
    ):
        P.run_processing.run(_payload("d.xlsx"))
    assert routes == ["office"]


def test_chain_routing():
    """Chaque étape route vers la queue dédiée de la suivante."""
    from alambic_workers.orchestration import processing as P

    seen = {}

    def _cap(name):
        return lambda args, queue: seen.__setitem__(name, queue)

    with (
        patch("alambic_workers.tasks.conversion.convert_document", side_effect=lambda p: p),
        patch.object(P.read_cab, "apply_async", side_effect=_cap("convert")),
    ):
        P.convert.run(_payload())
    with (
        patch("alambic_workers.tasks.barcode.read_cab_document", side_effect=lambda p: p),
        patch.object(P.read_ocr, "apply_async", side_effect=_cap("read_cab")),
    ):
        P.read_cab.run(_payload())
    with (
        patch("alambic_workers.tasks.ocr.read_ocr_document", side_effect=lambda p: p),
        patch.object(P.multi_doc, "apply_async", side_effect=_cap("read_ocr")),
    ):
        P.read_ocr.run(_payload())
    with (
        patch(
            "alambic_workers.tasks.multi_doc.detect_multi_doc",
            side_effect=lambda p: {**p, "children": []},
        ),
        patch.object(P.detect_split, "apply_async", side_effect=_cap("multi_doc")),
    ):
        P.multi_doc.run(_payload())
    with (
        patch(
            "alambic_workers.tasks.split.split_document",
            side_effect=lambda p: {**p, "children": []},
        ),
        patch.object(P.classify, "apply_async", side_effect=_cap("detect_split")),
    ):
        P.detect_split.run(_payload())
    with (
        patch("alambic_workers.tasks.classify.classify_document", side_effect=lambda p: p),
        patch.object(P.extract_fields, "apply_async", side_effect=_cap("classify")),
    ):
        P.classify.run(_payload())
    with (
        patch("alambic_workers.tasks.extract.extract_document", side_effect=lambda p: p),
        patch.object(P.finalize, "apply_async", side_effect=_cap("extract_fields")),
    ):
        P.extract_fields.run(_payload())

    assert seen["convert"] == "cab"
    assert seen["read_cab"] == "ocr"
    assert seen["read_ocr"] == "multidoc"
    assert seen["multi_doc"] == "normal"
    assert seen["detect_split"] == "classif"
    assert seen["classify"] == "extract"
    assert seen["extract_fields"] == "normal"


def test_chain_stops_on_discarded_document():
    """Une étape ne déclenche pas la suivante si le document est écarté."""
    from alambic_workers.orchestration import processing as P

    discarded = {"transaction": {"transactionId": "tx1"}, "document": None}
    with patch.object(P.read_ocr, "apply_async") as nxt:
        P.read_cab.run(discarded)
    nxt.assert_not_called()


def test_convert_real_conversion(core_db):
    """convert exécute la vraie conversion et enchaîne read_cab."""
    from alambic_core.models import Document

    from alambic_workers.orchestration import processing as P

    payload = {
        "transaction": {"transactionId": "tx1"},
        "document": {
            "documentId": "d1",
            "file": {"bucket": "alambic-work", "key": "__transactions__/a/c/tx1/d1.txt"},
        },
    }
    with patch.object(P.read_cab, "apply_async") as nxt:
        out = P.convert.run(payload)
    assert out["document"]["file"]["key"].endswith(".pdf")
    nxt.assert_called_once()
    with core_db() as s:
        assert s.get(Document, "d1").status == "CONVERTED_TO_PDF"


def test_classify_retries_on_transient_error():
    """Une panne LLM transitoire déclenche un retry Celery, pas l'extraction."""
    from alambic_core.pipeline.step import TransientStepError

    from alambic_workers.orchestration import processing as P

    extract_called = []

    class _Retry(Exception):
        pass

    with (
        patch(
            "alambic_workers.tasks.classify.classify_document",
            side_effect=TransientStepError("EdenAI 401"),
        ),
        patch.object(
            P.extract_fields, "apply_async", side_effect=lambda *a, **k: extract_called.append(1)
        ),
        patch.object(P.classify, "retry", side_effect=_Retry()),
    ):
        with pytest.raises(_Retry):
            P.classify.run(_payload())

    # L'extraction ne doit PAS avoir été enchaînée (le doc n'est pas classifié).
    assert extract_called == []
