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
            "expected_doctypes": (
                '[{"doctype_id":"CNI","required":true},'
                '{"doctype_id":"PERMIS","required":false}]'
            ),
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
        cfg = s.query(Config).filter_by(config_name="c1").first()
        assert cfg.need_validation is True
        assert cfg.expected_doctypes == [
            {"doctype_id": "CNI", "required": True},
            {"doctype_id": "PERMIS", "required": False},
        ]
        assert cfg.edenai_settings["ocr_provider"] == "ocr/ocr/mistral"
        assert cfg.ws["way_in"] == "S3"
        assert cfg.ws["export_url"] == "https://x.fr"
        assert json.loads(cfg.edenai_secret_enc)["secret_key"] == "SECRET1"
        assert json.loads(cfg.flower_enc)["token"] == "TOK1"


def test_config_tabs_present(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    # L'onglet « Reconnaissance » (réglages vision LLM) a été retiré : la
    # détection multi-document est désormais locale (OpenCV), sans config vision.
    for tab in ["Général", "IA", "Entrées", "Sorties"]:
        assert tab in page
    assert 'data-tab="reco"' not in page
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
        assert cfg.config_name == "x2"
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
        cfg = s.query(Config).filter_by(config_name="wsconf").first()
        assert cfg.ws["way_out"] == "WS"
        assert cfg.ws["export_url"] == "https://ws.fr"


def test_region_field_present_on_create(app_ctx):
    """Le champ région (pivot des endpoints) est présent à la création."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    assert 'name="region"' in page


def test_endpoints_exposed_as_optional_overrides(app_ctx):
    """Les endpoints OCR/classif/extract sont exposés (surcharges optionnelles)."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    # Exposés comme champs de surcharge (vides par défaut → repli région).
    assert 'name="ocr_end_point"' in page
    assert 'name="classifier_end_point"' in page
    assert 'name="extract_end_point"' in page
    # La détection multi-document est désormais locale (OpenCV) : plus d'endpoint vision.
    assert 'name="vision_end_point"' not in page
    # L'embedding reste servi en local (TEI) : pas d'endpoint embedding dans la config.
    assert 'name="embedding_end_point"' not in page
    # Plus aucune trace de l'ancien endpoint v2 obsolète.
    assert "v2/text/embeddings" not in page


def test_endpoint_placeholders_update_with_region(app_ctx):
    """La page expose le JS qui met à jour les placeholders d'endpoints selon la région."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    # La fonction de mise à jour des placeholders est présente et branchée.
    assert "updateEndpointPlaceholders" in page
    assert "edenai.run" in page  # construction de l'URL de base côté JS


def test_ocr_is_dynamic_select(app_ctx):
    """OCR est un select dynamique (data-feature) ; l'embedding n'est plus dans la config."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    assert 'data-feature="ocr"' in page
    # L'embedding est désormais servi en local (TEI), il ne figure plus dans la config.
    assert 'data-feature="embedding"' not in page


def test_duplicate_config_creates_inactive_copy(app_ctx):
    """Dupliquer une config crée une copie inactive avec un nom indexé."""
    app, Sess = app_ctx
    from alambic_core.models import Config

    with Sess() as s:
        s.add(Config(id="src", config_name="Ma Config", account_id="acc1", is_active=True))
        s.commit()

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/configs/").get_data(as_text=True))
    client.post("/configs/src/duplicate", data={"csrf_token": tok}, follow_redirects=True)

    with Sess() as s:
        copies = s.query(Config).filter(Config.config_name == "Ma Config (copie)").all()
        assert len(copies) == 1
        assert copies[0].is_active is False


def test_toggle_active(app_ctx):
    """Activer/désactiver bascule le drapeau is_active."""
    app, Sess = app_ctx
    from alambic_core.models import Config

    with Sess() as s:
        s.add(Config(id="c1", config_name="C", account_id="acc1", is_active=True))
        s.commit()

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/configs/").get_data(as_text=True))
    client.post("/configs/c1/toggle-active", data={"csrf_token": tok}, follow_redirects=True)

    with Sess() as s:
        assert s.get(Config, "c1").is_active is False


def test_config_fields_round_trip(app_ctx):
    """Les champs propagés sont parsés et persistés depuis le formulaire."""
    app, Sess = app_ctx
    import json

    from alambic_core.models import Config

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/configs/new").get_data(as_text=True))
    client.post(
        "/configs/new",
        data={
            "csrf_token": tok,
            "config_name": "CF",
            "account_id": "",
            "config_fields": json.dumps(
                [
                    {
                        "field_name": "email_from",
                        "field_label": "Expéditeur",
                        "source_type": "context",
                        "source_key": "from",
                        "default_value": "",
                    },
                    {"field_name": "", "source_type": "context"},  # ignoré
                ]
            ),
        },
        follow_redirects=True,
    )

    with Sess() as s:
        cfg = s.query(Config).filter_by(config_name="cf").first()
        assert cfg is not None
        assert len(cfg.config_fields) == 1
        assert cfg.config_fields[0]["field_name"] == "email_from"
        assert cfg.config_fields[0]["source_type"] == "context"


def test_consolidation_ws_round_trip(app_ctx):
    """Les définitions de WS de consolidation sont parsées et persistées."""
    import json

    from alambic_core.models import Config

    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/configs/new").get_data(as_text=True))
    client.post(
        "/configs/new",
        data={
            "csrf_token": tok,
            "config_name": "CW",
            "account_id": "",
            "consolidation_ws": json.dumps(
                [
                    {
                        "name": "verif_compte",
                        "target_field": "@facture:numero_compte",
                        "url": "https://api.banque.fr/comptes/{value}",
                        "method": "GET",
                        "response_status_path": "exists",
                        "response_mapping": {"titulaire": "compte_titulaire"},
                        "on_failure": "skip",
                    },
                    {"name": "", "url": "https://x.fr"},  # ignoré (pas de nom)
                ]
            ),
        },
        follow_redirects=True,
    )
    with Sess() as s:
        cfg = s.query(Config).filter_by(config_name="cw").first()
        assert cfg is not None
        assert len(cfg.consolidation_ws) == 1
        ws = cfg.consolidation_ws[0]
        assert ws["name"] == "verif_compte"
        assert ws["target_field"] == "@facture:numero_compte"
        assert ws["response_mapping"] == {"titulaire": "compte_titulaire"}
        assert ws["on_failure"] == "skip"


def test_edit_preserves_edenai_settings_when_fields_absent(app_ctx):
    """Éditer une config sans renvoyer les champs EdenAI ne doit PAS les effacer.

    Régression : un select dont les options sont injectées en JS (ou désactivé)
    n'est pas soumis ; sans préservation, la config EdenAI était effacée à chaque
    sauvegarde, faisant échouer les appels (symptôme « la clé ne marche plus »).
    """
    from alambic_core.models import Config

    from alambic_ui.config_schema import apply_form_to_config

    _, Sess = app_ctx
    with Sess() as s:
        c = Config(
            config_name="C",
            edenai_secret_enc='{"secret_key": "K"}',
            edenai_settings={
                "region": "eu",
                "classifier_provider": "mistral",
                "extract_provider": "mistral",
                "classifier_confidence_level": "0.7",
            },
        )
        s.add(c)
        s.commit()
        cid = c.id

    # Formulaire minimal : on change le nom, on ne renvoie AUCUN champ edenai.
    with Sess() as s:
        c = s.get(Config, cid)
        apply_form_to_config(c, {"config_name": "C2", "edenai_secret_key": ""})
        s.commit()

    with Sess() as s:
        c = s.get(Config, cid)
        st = c.edenai_settings or {}
        assert st.get("region") == "eu"
        assert st.get("classifier_provider") == "mistral"
        assert st.get("extract_provider") == "mistral"
        assert st.get("classifier_confidence_level") == "0.7"


def test_edit_respects_explicit_field_change(app_ctx):
    """Un champ PRÉSENT dans le formulaire est respecté (modifié ou vidé)."""
    from alambic_core.models import Config

    from alambic_ui.config_schema import apply_form_to_config

    _, Sess = app_ctx
    with Sess() as s:
        c = Config(
            config_name="C",
            edenai_settings={"classifier_provider": "mistral", "extract_provider": "mistral"},
        )
        s.add(c)
        s.commit()
        cid = c.id

    with Sess() as s:
        c = s.get(Config, cid)
        apply_form_to_config(
            c,
            {
                "config_name": "C",
                "classifier_provider": "",  # vidé volontairement
                "extract_provider": "openai",  # changé
            },
        )
        s.commit()

    with Sess() as s:
        c = s.get(Config, cid)
        st = c.edenai_settings or {}
        assert st.get("classifier_provider") == ""
        assert st.get("extract_provider") == "openai"


def test_placeholder_secret_does_not_overwrite_key(app_ctx):
    """Une valeur de puces (autofill) ne doit pas écraser la vraie clé EdenAI."""
    from alambic_ui.config_schema import _is_placeholder_secret, apply_form_to_config

    # Le helper reconnaît les puces.
    assert _is_placeholder_secret("••••••••") is True
    assert _is_placeholder_secret("eyJhbGci.real.key") is False

    _app, Sess = app_ctx
    from alambic_core.models import Config

    real = "eyJhbGciOiJIUzI1NiJ9.PAYLOAD.sig"
    with Sess() as s:
        cfg = Config(id="cfg-sec", config_name="c")
        apply_form_to_config(cfg, {"config_name": "c", "edenai_secret_key": real})
        s.add(cfg)
        s.commit()

    # Re-sauvegarde avec des puces → la vraie clé est préservée.
    with Sess() as s:
        cfg = s.get(Config, "cfg-sec")
        apply_form_to_config(cfg, {"config_name": "c", "edenai_secret_key": "••••••••"})
        s.commit()
    with Sess() as s:
        assert real in s.get(Config, "cfg-sec").edenai_secret_enc


def test_ocr_engine_selector_present(app_ctx):
    """Le sélecteur de moteur OCR (EdenAI / Tesseract) est exposé dans la config."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    assert 'name="ocr_engine"' in page
    assert "Tesseract" in page


def test_ocr_engine_persists(app_ctx):
    """Le moteur OCR choisi est enregistré dans edenai_settings."""
    from alambic_ui.config_schema import apply_form_to_config

    _app, Sess = app_ctx
    from alambic_core.models import Config

    with Sess() as s:
        cfg = Config(id="cfg-eng", config_name="c")
        apply_form_to_config(cfg, {"config_name": "c", "ocr_engine": "tesseract"})
        s.add(cfg)
        s.commit()
    with Sess() as s:
        assert s.get(Config, "cfg-eng").edenai_settings.get("ocr_engine") == "tesseract"


def test_ocr_preprocess_and_rotation_options(app_ctx):
    """Les options de prétraitement, cascade et rotation sont exposées avec aide."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/configs/new").get_data(as_text=True)
    # Options présentes.
    assert 'name="ocr_preprocess"' in page
    assert 'name="ocr_rotation"' in page
    assert "Cascade" in page
    # Explications présentes (l'utilisateur est guidé).
    assert "Multi-profils" in page
    assert "à l'endroit" in page  # aide de la rotation


def test_ocr_advanced_options_persist(app_ctx):
    from alambic_ui.config_schema import apply_form_to_config

    _app, Sess = app_ctx
    from alambic_core.models import Config

    with Sess() as s:
        cfg = Config(id="cfg-ocr", config_name="c")
        apply_form_to_config(cfg, {"config_name": "c", "ocr_engine": "cascade",
                                   "ocr_preprocess": "multi", "ocr_rotation": "on"})
        s.add(cfg)
        s.commit()
    with Sess() as s:
        st = s.get(Config, "cfg-ocr").edenai_settings
        assert st.get("ocr_engine") == "cascade"
        assert st.get("ocr_preprocess") == "multi"
        assert st.get("ocr_rotation") is True
