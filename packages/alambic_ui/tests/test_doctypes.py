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


def test_generate_fields_uses_account_config(app_ctx, monkeypatch):
    """La génération depuis PDF résout endpoint+clé via la config du compte."""
    import io

    from alambic_core.models import Account, Config

    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="a1", account_name="ACME", edenai_secret_key="cle-eden"))
        s.add(
            Config(
                id="c1",
                config_name="C",
                account_id="a1",
                is_active=True,
                edenai_settings={
                    "region": "eu",
                    "extract_provider": "mistral",
                    "extract_model": "mistral-large-latest",
                },
            )
        )
        s.commit()

    # Mock du générateur : on vérifie qu'il reçoit endpoint + clé non vides.
    import alambic_ui.doctype_generator as gen

    captured = {}

    def fake_generate(pdf_bytes, *, endpoint, secret_key, **kw):
        captured["endpoint"] = endpoint
        captured["secret_key"] = secret_key
        return [{"field_name": "numero", "field_type": "string", "field_description": ""}]

    monkeypatch.setattr(gen, "generate_fields_from_pdf", fake_generate)
    # Patch aussi la référence importée dans la route.
    import alambic_ui.blueprints.doctypes as dt_bp

    monkeypatch.setattr(dt_bp, "generate_fields_from_pdf", fake_generate, raising=False)

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/doctypes/new").get_data(as_text=True))
    res = client.post(
        "/doctypes/generate-fields",
        data={
            "csrf_token": tok,
            "account_id": "a1",
            "pdf": (io.BytesIO(b"%PDF-1.4 fake"), "test.pdf"),
        },
        content_type="multipart/form-data",
    )
    body = res.get_json()
    assert "fields" in body, body
    assert body["fields"][0]["field_name"] == "numero"
    assert captured["secret_key"] == "cle-eden"
    assert captured["endpoint"]


def test_generate_parses_extraction_strategy():
    """La génération extrait anchors/direction/regexp/use_ia avec validation."""
    import json

    from alambic_ui.doctype_generator import _parse_llm_response

    content = json.dumps(
        {
            "document_type": "facture",
            "fields": [
                {
                    "field_name": "numero",
                    "field_type": "string",
                    "field_description": "N°",
                    "anchors": "Facture N°",
                    "direction": "right",
                    "regexp": r"[A-Z0-9-]+",
                    "use_ia": False,
                },
                {
                    "field_name": "conditions",
                    "field_type": "string",
                    "field_description": "texte libre",
                    "anchors": "",
                    "direction": "",
                    "regexp": "",
                    "use_ia": True,
                },
                {
                    "field_name": "bad_dir",
                    "field_type": "string",
                    "field_description": "x",
                    "anchors": "Ref",
                    "direction": "diagonale",
                    "regexp": "",
                    "use_ia": False,
                },
                {
                    "field_name": "dir_sans_ancre",
                    "field_type": "string",
                    "field_description": "x",
                    "anchors": "",
                    "direction": "right",
                    "regexp": "",
                    "use_ia": False,
                },
            ],
        },
        ensure_ascii=False,
    )
    resp = {"choices": [{"message": {"content": content}}]}
    fields = {f["field_name"]: f for f in _parse_llm_response(resp)}

    # Règle déterministe complète.
    assert fields["numero"]["anchors"] == "Facture N°"
    assert fields["numero"]["direction"] == "right"
    assert fields["numero"]["regexp"] == r"[A-Z0-9-]+"
    assert fields["numero"]["use_ia"] is False

    # use_ia → règles vidées (cohérence).
    assert fields["conditions"]["use_ia"] is True
    assert fields["conditions"]["anchors"] == ""
    assert fields["conditions"]["direction"] == ""

    # Direction invalide → vidée.
    assert fields["bad_dir"]["direction"] == ""
    # Direction sans ancre → vidée (garde-fou).
    assert fields["dir_sans_ancre"]["direction"] == ""


def test_doctype_name_normalized_to_snake_case(app_ctx):
    """Le nom de doctype et les field_name sont stockés en snake_case."""
    from alambic_core.models import Doctype
    from conftest import csrf, login

    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/doctypes/new").get_data(as_text=True))
    client.post(
        "/doctypes/new",
        data={
            "doctype_name": "Carte Nationale d'Identité",
            "account_id": "",
            "fields-0-field_name": "Numéro Pièce",
            "fields-0-field_type": "string",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        dt = s.query(Doctype).filter_by(doctype_name="carte_nationale_d_identite").first()
        assert dt is not None
        import json

        content = json.loads(dt.json_content)
        names = [f["field_name"] for f in content.get("fields", [])]
        assert "numero_piece" in names
