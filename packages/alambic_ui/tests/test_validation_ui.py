"""Tests du rendu UI : tri, pagination, sélection groupée, modale de validation."""

from __future__ import annotations

from conftest import login


def _seed(Sess):
    from alambic_core.models import Document, Transaction

    with Sess() as s:
        s.add(Transaction(id="trx-1", status="PENDING_VALIDATION", process="P", account_id="acc1"))
        s.add(Document(id="d1", transaction_id="trx-1", status="PENDING_VALIDATION", process="P"))
        s.commit()


def test_table_has_sortable_headers(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # En-têtes triables (liens avec sort_by).
    assert "sort_by=creation_date" in page
    assert "sort_by=status" in page


def test_table_has_bulk_select_and_pagination(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    assert 'id="check-all"' in page
    assert 'name="transaction_ids"' in page
    assert "delete-bulk" in page
    assert "Page 1" in page


def test_validation_modal_present(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    assert 'id="val-modal"' in page
    assert "openValidation" in page
    assert "val-pdf-canvas" in page


def test_documents_fragment_has_validate_button(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    frag = client.get("/transactions/trx-1/documents").get_data(as_text=True)
    assert "openValidation('trx-1', 'd1')" in frag
    assert "Valider" in frag


def test_validation_list_route(app_ctx):
    """La route validation-list renvoie les documents avec leur état."""
    app, Sess = app_ctx
    from alambic_core.models import Document, Transaction

    with Sess() as s:
        s.add(Transaction(id="trx-9", status="PENDING_VALIDATION", process="P", account_id="acc1"))
        s.add(
            Document(
                id="a1", transaction_id="trx-9", status="PENDING_VALIDATION",
                process="P", doctype="facture",
            )
        )
        s.add(
            Document(
                id="a2", transaction_id="trx-9", status="VALIDATED",
                process="P", doctype="contrat",
            )
        )
        s.add(Document(id="a3", transaction_id="trx-9", status="DEPRECATED", process="P"))
        s.commit()

    client = app.test_client()
    login(client)
    data = client.get("/transactions/trx-9/validation-list").get_json()
    docs = {d["id"]: d for d in data["documents"]}
    # a3 (DEPRECATED) exclu.
    assert set(docs) == {"a1", "a2"}
    assert docs["a1"]["state"] == "pending"
    assert docs["a2"]["state"] == "validated"


def test_modal_has_sidebar(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    assert 'id="val-doc-list"' in page
    assert "val-sidebar" in page


def test_modal_fullscreen_and_zoom(app_ctx):
    """La modale est en plein écran et propose des contrôles de zoom PDF."""
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # Plein écran.
    assert "98vw" in page and "96vh" in page
    # Contrôles de zoom.
    assert "valZoom(" in page
    assert "valZoomFit()" in page
    assert 'id="val-pdf-zoom"' in page


def test_indexes_route_returns_all_doctype_fields(app_ctx):
    """La route /indexes renvoie tous les champs du doctype, même sans extraction."""
    import json

    app, Sess = app_ctx
    from alambic_core.models import Doctype, Document, Transaction

    fields = {"fields": [
        {"field_name": "type_carte", "field_description": "Type"},
        {"field_name": "num_carte", "field_description": "Numéro"},
    ]}
    with Sess() as s:
        s.add(Doctype(id="dt1", doctype_name="carte", json_content=json.dumps(fields)))
        s.add(Transaction(id="tx1", status="PENDING_VALIDATION", process="P", account_id="acc1"))
        s.add(Document(id="doc1", transaction_id="tx1", doctype="carte",
                       status="PENDING_VALIDATION", process="P"))
        s.commit()

    client = app.test_client()
    login(client)
    data = client.get("/transactions/documents/doc1/indexes").get_json()
    # Tous les champs du doctype présents, même sans valeur extraite.
    names = {f["index_name"] for f in data["indexes"]}
    assert names == {"type_carte", "num_carte"}
    assert all(f["index_value"] == "" for f in data["indexes"])
