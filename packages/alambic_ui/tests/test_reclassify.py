"""Tests des routes de reclassification manuelle (transactions)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import csrf, login

# Ces tests vérifient l'intégration UI → workers (relance des tâches) : ils
# nécessitent le package workers importable.
pytest.importorskip("alambic_workers.orchestration.processing")


def _seed(Sess):
    from alambic_core.models import Config, Doctype, Document, Transaction

    with Sess() as s:
        s.add(Config(id="cfg1", config_name="c", account_id="acc1",
                     expected_doctypes=[{"doctype_id": "dt1", "required": True},
                                        {"doctype_id": "dt2", "required": False}],
                     general={"classifier_let_it_guess": True}))
        s.add(Doctype(id="dt1", doctype_name="carte", account_id="acc1"))
        s.add(Doctype(id="dt2", doctype_name="passeport", account_id="acc1"))
        s.add(Transaction(id="tx1", status="W", process="X",
                          config_id="cfg1", account_id="acc1"))
        s.add(Document(id="doc1", transaction_id="tx1", status="UNRECOGNIZED",
                       process="CLASSIFIER", doctype="unknown",
                       bucket_name="b", object_key="k.pdf"))
        s.commit()


def test_doctypes_route_lists_config_types(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    resp = client.get("/transactions/documents/doc1/doctypes")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data["doctypes"]) == {"carte", "passeport"}
    assert data["let_it_guess"] is True
    assert data["current"] == "unknown"


def test_reclassify_sets_type_and_queues_extract(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    with patch("alambic_workers.orchestration.processing.extract_fields") as ef:
        resp = client.post(
            "/transactions/documents/doc1/reclassify",
            json={"doctype": "passeport"},
            headers={"X-CSRFToken": tok},
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["doctype"] == "passeport"
    ef.apply_async.assert_called_once()

    from alambic_core.models import Document

    with Sess() as s:
        assert s.get(Document, "doc1").doctype == "passeport"


def test_reclassify_rejects_unknown_doctype(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    resp = client.post(
        "/transactions/documents/doc1/reclassify",
        json={"doctype": "type_inexistant"},
        headers={"X-CSRFToken": tok},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "doctype_not_allowed"


def test_reclassify_guess_requeues_classify(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    with patch("alambic_workers.orchestration.processing.classify") as cl:
        resp = client.post(
            "/transactions/documents/doc1/reclassify",
            json={"doctype": "__guess__"},
            headers={"X-CSRFToken": tok},
        )
    assert resp.status_code == 200
    cl.apply_async.assert_called_once()
    from alambic_core.models import Document

    with Sess() as s:
        assert s.get(Document, "doc1").process == "OCR_READER"
