"""Tests du blueprint transactions (dépôt → clé d'ingestion)."""

from __future__ import annotations

import io

from alambic_core.models import Account, Config
from conftest import csrf, login


def test_transactions_requires_admin(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")
    r = client.get("/transactions/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()


def test_index_lists_configs(app_ctx):
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A1"))
        s.add(Config(id="cfg1", config_name="Config 1", account_id="acc1"))
        s.commit()
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    assert "Config 1" in page


def test_upload_deposits_with_correct_key(app_ctx, monkeypatch):
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A1"))
        s.add(Config(id="cfg1", config_name="C1", account_id="acc1"))
        s.commit()

    captured = []
    import alambic_ui.blueprints.transactions as txmod

    monkeypatch.setattr(
        txmod,
        "put_bytes",
        lambda bucket, key, content, metadata=None: captured.append((bucket, key)),
    )

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    client.post(
        "/transactions/upload",
        data={
            "config_id": "cfg1",
            "csrf_token": tok,
            "files": [(io.BytesIO(b"data"), "doc.pdf")],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert len(captured) == 1
    bucket, key = captured[0]
    assert key == "__uploads__/acc1/cfg1/UI_IMPORT/doc.pdf"


def test_upload_without_config(app_ctx):
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A1"))
        s.add(Config(id="cfg1", config_name="C1", account_id="acc1"))
        s.commit()
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    r = client.post(
        "/transactions/upload",
        data={
            "config_id": "",
            "csrf_token": tok,
            "files": [(io.BytesIO(b"x"), "f.pdf")],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "configuration" in r.get_data(as_text=True).lower()


def test_exported_document_is_read_only(app_ctx):
    """Un document EXPORTED ne peut être ni validé ni enregistré (409)."""
    from alambic_core.models import Document, Transaction

    app, Sess = app_ctx
    with Sess() as s:
        s.add(Transaction(id="tx-e", status="COMPLETED", process="X", account_id=""))
        s.add(
            Document(
                id="doc-e",
                transaction_id="tx-e",
                status="EXPORTED",
                process="X",
                doctype="facture",
            )
        )
        s.commit()

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    hdr = {"X-CSRFToken": tok}

    # Validation refusée.
    r = client.post(
        "/transactions/documents/doc-e/validate",
        json={"indexes": [{"name": "x", "value": "1"}]},
        headers=hdr,
    )
    assert r.status_code == 409
    assert r.get_json()["error"] == "document_exporte"

    # Enregistrement refusé aussi.
    r2 = client.post(
        "/transactions/documents/doc-e/save",
        json={"indexes": [{"name": "x", "value": "1"}]},
        headers=hdr,
    )
    assert r2.status_code == 409


def test_validated_document_still_editable(app_ctx):
    """Un document VALIDATED (non exporté) reste validable."""
    from alambic_core.models import Document, Transaction

    app, Sess = app_ctx
    with Sess() as s:
        s.add(Transaction(id="tx-v", status="COMPLETED", process="X", account_id=""))
        s.add(
            Document(
                id="doc-v",
                transaction_id="tx-v",
                status="VALIDATED",
                process="X",
                doctype="facture",
            )
        )
        s.commit()

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    r = client.post(
        "/transactions/documents/doc-v/validate",
        json={"indexes": [{"name": "x", "value": "1"}]},
        headers={"X-CSRFToken": tok},
    )
    assert r.status_code == 200
