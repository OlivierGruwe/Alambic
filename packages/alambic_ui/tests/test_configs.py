"""Tests du CRUD configs : 6 onglets, blocs JSONB, secrets masqués."""

from __future__ import annotations

import json

from alambic_core.models import Config
from conftest import csrf, login


def test_configs_requires_admin(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")
    r = client.get("/configs/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()


def test_create_config_maps_to_blocks(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/configs/new").get_data(as_text=True))
    client.post(
        "/configs/new",
        data={
            "config_name": "C1",
            "account_id": "",
            "doctype_id": "",
            "need_validation": "on",
            "csrf_token": tok,
            "auto_validation_threshold": "1",
            "completeness_check": "on",
            "ocr_provider": "ocr/ocr/mistral",
            "extract_model": "mistral-small",
            "way_in": "S3",
            "export_url": "https://x.fr",
            "export_auth_type": "bearer",
            "edenai_secret_key": "SECRET1",
            "export_auth_token": "TOK1",
        },
        follow_redirects=True,
    )
    with Sess() as s:
        cfg = s.query(Config).filter_by(config_name="C1").first()
        assert cfg.need_validation is True
        assert cfg.general["completeness_check"] is True
        assert cfg.edenai_settings["ocr_provider"] == "ocr/ocr/mistral"
        assert cfg.ws["way_in"] == "S3"
        assert cfg.ws["export_url"] == "https://x.fr"
        assert json.loads(cfg.edenai_secret_enc)["secret_key"] == "SECRET1"
        assert json.loads(cfg.flower_enc)["token"] == "TOK1"


def test_five_tabs_present(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    for tab in ["Général", "IA", "Reconnaissance", "Entrées", "Sorties"]:
        assert tab in page
    # Export WS est maintenant une section de Sorties, pas un onglet
    assert 'data-tab="export"' not in page
    assert 'data-way="WS"' in page


def test_secrets_never_displayed(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        cfg = Config(id="c1", config_name="X")
        cfg.edenai_secret_enc = json.dumps({"secret_key": "TOPSECRET"})
        s.add(cfg)
        s.commit()
    page = client.get("/configs/c1/edit").get_data(as_text=True)
    assert "TOPSECRET" not in page


def test_edit_empty_secret_preserved(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        cfg = Config(id="c1", config_name="X")
        cfg.edenai_secret_enc = json.dumps({"secret_key": "KEEP"})
        s.add(cfg)
        s.commit()
    tok = csrf(client.get("/configs/c1/edit").get_data(as_text=True))
    client.post(
        "/configs/c1/edit",
        data={
            "config_name": "X2",
            "account_id": "",
            "doctype_id": "",
            "csrf_token": tok,  # pas de secret saisi
        },
        follow_redirects=True,
    )
    with Sess() as s:
        cfg = s.get(Config, "c1")
        assert cfg.config_name == "X2"
        assert json.loads(cfg.edenai_secret_enc)["secret_key"] == "KEEP"


def test_delete_config(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        s.add(Config(id="c1", config_name="X"))
        s.commit()
    tok = csrf(client.get("/configs/").get_data(as_text=True))
    client.post("/configs/c1/delete", data={"csrf_token": tok}, follow_redirects=True)
    with Sess() as s:
        assert s.get(Config, "c1") is None


def test_doctype_filter_by_account(app_ctx):
    app, Sess = app_ctx
    from alambic_core.models import Account, Doctype

    with Sess() as s:
        s.add(Account(id="accA", account_name="A"))
        s.add(Account(id="accB", account_name="B"))
        s.add(Doctype(id="dpub", doctype_name="public_dt", is_public=True))
        s.add(Doctype(id="dA", doctype_name="dt_A", account_id="accA"))
        s.add(Doctype(id="dB", doctype_name="dt_B", account_id="accB"))
        s.commit()
    client = app.test_client()
    login(client)
    import json

    r = client.get("/configs/doctypes-for-account?account_id=accA")
    names = [d["name"] for d in json.loads(r.get_data(as_text=True))["doctypes"]]
    assert "public_dt" in names and "dt_A" in names and "dt_B" not in names


def test_account_edenai_detection(app_ctx):
    app, Sess = app_ctx
    import json

    from alambic_core.models import Account

    with Sess() as s:
        a = Account(id="accK", account_name="K")
        a.edenai_secret_key = "THEKEY"
        s.add(a)
        s.add(Account(id="accNo", account_name="NoKey"))
        s.commit()
    client = app.test_client()
    login(client)
    r = client.get("/configs/account-edenai?account_id=accK")
    assert json.loads(r.get_data(as_text=True))["has_account_key"] is True
    r = client.get("/configs/account-edenai?account_id=accNo")
    assert json.loads(r.get_data(as_text=True))["has_account_key"] is False


def test_export_ws_as_output_mode(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/configs/new").get_data(as_text=True))
    client.post(
        "/configs/new",
        data={
            "config_name": "WSConf",
            "account_id": "",
            "doctype_id": "",
            "way_out": "WS",
            "export_url": "https://ws.fr",
            "export_auth_type": "bearer",
            "export_auth_token": "T",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        cfg = s.query(Config).filter_by(config_name="WSConf").first()
        assert cfg.ws["way_out"] == "WS"
        assert cfg.ws["export_url"] == "https://ws.fr"


def test_region_field_present_on_create(app_ctx):
    """Le champ région (pivot des endpoints) est présent à la création."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    assert 'name="region"' in page


def test_endpoints_not_saisis_in_form(app_ctx):
    """Les endpoints ne sont plus des champs saisis (construits depuis la région)."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    assert 'name="ocr_end_point"' not in page
    assert 'name="embedding_end_point"' not in page
    # Plus aucune trace de l'ancien endpoint v2 obsolète.
    assert "v2/text/embeddings" not in page


def test_ocr_is_dynamic_select(app_ctx):
    """OCR est un select dynamique (data-feature) ; l'embedding n'est plus dans la config."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    assert 'data-feature="ocr"' in page
    # L'embedding est désormais servi en local (TEI), il ne figure plus dans la config.
    assert 'data-feature="embedding"' not in page
