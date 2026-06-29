"""Tests des routes de validation des index et de la suppression groupée."""

from __future__ import annotations

import json

from conftest import csrf, login


def _seed(Sess):
    """Crée une transaction avec un document en attente de validation + index."""
    from alambic_core.models import Document, DocumentIndex, Transaction

    with Sess() as s:
        s.add(Transaction(id="tx1", status="PENDING_VALIDATION", process="X", account_id="acc1"))
        s.add(
            Document(
                id="doc1",
                transaction_id="tx1",
                status="PENDING_VALIDATION",
                process="X",
                doctype="facture",
                doctype_desc="Une facture",
                bucket_name="work",
                object_key="path/doc1.pdf",
            )
        )
        s.add(
            DocumentIndex(
                document_id="doc1",
                index_type="extracted",
                index_name="montant",
                index_value="100",
                index_score="0.8",
                index_desc="le montant",
            )
        )
        s.commit()


def test_document_indexes_returns_json(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)

    r = client.get("/transactions/documents/doc1/indexes")
    assert r.status_code == 200
    data = r.get_json()
    assert data["document_id"] == "doc1"
    assert data["doctype"] == "facture"
    assert len(data["indexes"]) == 1
    assert data["indexes"][0]["index_name"] == "montant"


def test_document_validate_sets_status_and_saves(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    token = csrf(client.get("/transactions/").get_data(as_text=True))

    r = client.post(
        "/transactions/documents/doc1/validate",
        data=json.dumps({"indexes": [{"index_name": "montant", "index_value": "150"}]}),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert r.status_code == 200
    assert r.get_json()["status"] == "VALIDATED"

    from alambic_core.models import Document, DocumentIndex

    with Sess() as s:
        doc = s.get(Document, "doc1")
        assert doc.status == "VALIDATED"
        idx = s.query(DocumentIndex).filter(DocumentIndex.document_id == "doc1").all()
        assert len(idx) == 1
        assert idx[0].index_value == "150"
        assert idx[0].index_score == "1.0"  # correction humaine


def test_document_save_does_not_validate(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    token = csrf(client.get("/transactions/").get_data(as_text=True))

    r = client.post(
        "/transactions/documents/doc1/save",
        data=json.dumps({"indexes": [{"index_name": "montant", "index_value": "200"}]}),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert r.status_code == 200
    assert r.get_json()["saved"] == 1

    from alambic_core.models import Document

    with Sess() as s:
        doc = s.get(Document, "doc1")
        assert doc.status == "PENDING_VALIDATION"  # PAS validé


def test_document_indexes_404_unknown(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    r = client.get("/transactions/documents/inconnu/indexes")
    assert r.status_code == 404


def test_delete_bulk_removes_selected(app_ctx, monkeypatch):
    app, Sess = app_ctx
    _seed(Sess)

    # Mocker la suppression (pas de Garage en test).
    from alambic_core.services import deletion

    called = []
    monkeypatch.setattr(deletion, "delete_transaction", lambda tx_id: called.append(tx_id))

    client = app.test_client()
    login(client)
    token = csrf(client.get("/transactions/").get_data(as_text=True))
    r = client.post(
        "/transactions/delete-bulk",
        data={"transaction_ids": ["tx1"], "csrf_token": token},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert called == ["tx1"]
