"""Tests du CRUD doctypes et de l'éditeur de champs."""

from __future__ import annotations

import json

from alambic_core.models import Doctype
from conftest import csrf, login


def test_doctypes_requires_admin(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")
    r = client.get("/doctypes/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()


def test_create_doctype_with_fields(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/doctypes/new").get_data(as_text=True))
    client.post(
        "/doctypes/new",
        data={
            "doctype_name": "facture",
            "account_id": "",
            "csrf_token": tok,
            "fields-0-field_name": "numero",
            "fields-0-field_type": "string",
            "fields-0-required": "on",
            "fields-0-regexp": "[0-9]+",
            "fields-1-field_name": "montant",
            "fields-1-field_type": "float",
        },
        follow_redirects=True,
    )
    with Sess() as s:
        dt = s.query(Doctype).filter_by(doctype_name="facture").first()
        content = json.loads(dt.json_content)
        assert content["document_type"] == "facture"
        assert len(content["fields"]) == 2
        assert content["fields"][0]["required"] == 1
        assert content["fields"][0]["regexp"] == "[0-9]+"
        assert content["fields"][1]["required"] == 0


def test_empty_field_ignored(app_ctx):
    """Un champ sans nom (ligne ajoutée puis vide) est ignoré."""
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/doctypes/new").get_data(as_text=True))
    client.post(
        "/doctypes/new",
        data={
            "doctype_name": "doc",
            "account_id": "",
            "csrf_token": tok,
            "fields-0-field_name": "champ1",
            "fields-0-field_type": "string",
            "fields-1-field_name": "",
            "fields-1-field_type": "string",  # vide
        },
        follow_redirects=True,
    )
    with Sess() as s:
        dt = s.query(Doctype).filter_by(doctype_name="doc").first()
        content = json.loads(dt.json_content)
        assert len(content["fields"]) == 1  # le champ vide est ignoré


def test_edit_doctype_prefills(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    content = json.dumps(
        {
            "document_type": "x",
            "fields": [
                {
                    "field_name": "champ_a",
                    "field_type": "string",
                    "regexp": "abc",
                    "required": 1,
                    "field_description": "",
                    "field_format": "",
                    "bcr_type": "",
                    "default_value": "",
                    "black_words": "",
                    "strategy": "",
                    "anchors": "",
                    "direction": "right",
                    "max_distance": "",
                    "page_zone": "",
                    "priority": "",
                    "is_separator": 0,
                    "is_hidden": 0,
                    "use_ia": 0,
                    "block_search": 0,
                },
            ],
        }
    )
    with Sess() as s:
        s.add(Doctype(id="d1", doctype_name="x", json_content=content))
        s.commit()
    page = client.get("/doctypes/d1/edit").get_data(as_text=True)
    assert "champ_a" in page
    assert 'value="abc"' in page


def test_delete_doctype(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        s.add(Doctype(id="d1", doctype_name="x", json_content=""))
        s.commit()
    tok = csrf(client.get("/doctypes/").get_data(as_text=True))
    client.post("/doctypes/d1/delete", data={"csrf_token": tok}, follow_redirects=True)
    with Sess() as s:
        assert s.get(Doctype, "d1") is None
