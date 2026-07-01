"""Tests du CRUD des clés API et du WS d'ingestion (auth par clé)."""

from __future__ import annotations

from conftest import csrf, login


def test_create_key_shows_secret_once(app_ctx):
    """La création génère une clé et affiche sa valeur en clair une seule fois."""
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/apikeys/").get_data(as_text=True))
    resp = client.post(
        "/apikeys/create",
        data={"apikey_name": "WS Test", "is_admin": "on", "validity": "30", "csrf_token": tok},
        follow_redirects=True,
    )
    page = resp.get_data(as_text=True)
    # La valeur en clair (préfixe alb_) apparaît dans le bandeau de création.
    assert "alb_" in page
    assert "copiez-la" in page.lower() or "copiez" in page.lower()

    from alambic_core.models import ApiKey

    with Sess() as s:
        keys = s.query(ApiKey).all()
        assert len(keys) == 1
        # La valeur en clair n'est PAS stockée (seulement le hash).
        assert len(keys[0].key_hash) == 64
        assert keys[0].apikey_name == "WS Test"


def test_list_shows_prefix_not_value(app_ctx):
    """La liste affiche le préfixe, jamais la valeur complète."""
    app, Sess = app_ctx
    from alambic_core.models import ApiKey
    from alambic_core.services.api_keys import generate_key

    gen = generate_key()
    with Sess() as s:
        s.add(ApiKey(id="k1", apikey_name="Existante", key_hash=gen.key_hash,
                     key_prefix=gen.key_prefix, is_admin=True))
        s.commit()

    client = app.test_client()
    login(client)
    page = client.get("/apikeys/").get_data(as_text=True)
    assert "Existante" in page
    assert gen.key_prefix in page
    # La valeur complète n'est jamais affichée.
    assert gen.plaintext not in page


def test_toggle_and_delete(app_ctx):
    app, Sess = app_ctx
    from alambic_core.models import ApiKey
    from alambic_core.services.api_keys import generate_key

    gen = generate_key()
    with Sess() as s:
        s.add(ApiKey(id="k1", apikey_name="K", key_hash=gen.key_hash,
                     key_prefix=gen.key_prefix, is_admin=True, is_active=True))
        s.commit()

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/apikeys/").get_data(as_text=True))
    client.post("/apikeys/k1/toggle", data={"csrf_token": tok}, follow_redirects=True)
    with Sess() as s:
        assert s.get(ApiKey, "k1").is_active is False

    client.post("/apikeys/k1/delete", data={"csrf_token": tok}, follow_redirects=True)
    with Sess() as s:
        assert s.get(ApiKey, "k1") is None


def test_ws_configs_requires_key(app_ctx):
    """GET /api/v1/configs sans clé → 401."""
    app, _ = app_ctx
    client = app.test_client()
    resp = client.get("/api/v1/configs")
    assert resp.status_code == 401


def test_ws_configs_lists_with_admin_key(app_ctx):
    """Une clé admin voit toutes les configs."""
    app, Sess = app_ctx
    from alambic_core.models import ApiKey, Config
    from alambic_core.services.api_keys import generate_key

    gen = generate_key()
    with Sess() as s:
        s.add(ApiKey(id="k1", apikey_name="Admin", key_hash=gen.key_hash,
                     key_prefix=gen.key_prefix, is_admin=True, is_active=True))
        s.add(Config(id="cfg1", config_name="Conf A", account_id="acc1"))
        s.add(Config(id="cfg2", config_name="Conf B", account_id="acc2"))
        s.commit()

    client = app.test_client()
    resp = client.get("/api/v1/configs", headers={"Authorization": f"Bearer {gen.plaintext}"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 2


def test_ws_ingest_deposits_document(app_ctx):
    """POST /api/v1/ingest avec clé valide dépose le document (202)."""
    import io
    from unittest.mock import patch

    app, Sess = app_ctx
    from alambic_core.models import ApiKey, Config
    from alambic_core.services.api_keys import generate_key

    gen = generate_key()
    with Sess() as s:
        s.add(ApiKey(id="k1", apikey_name="Admin", key_hash=gen.key_hash,
                     key_prefix=gen.key_prefix, is_admin=True, is_active=True))
        s.add(Config(id="cfg1", config_name="Conf", account_id="acc1"))
        s.commit()

    client = app.test_client()
    with patch("alambic_ui.blueprints.api.put_bytes", return_value=None) as pb:
        resp = client.post(
            "/api/v1/ingest",
            headers={"Authorization": f"Bearer {gen.plaintext}"},
            data={"config_id": "cfg1", "file": (io.BytesIO(b"%PDF-fake"), "doc.pdf")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 202
    assert resp.get_json()["status"] == "accepted"
    pb.assert_called_once()


def test_ws_ingest_rejects_foreign_config(app_ctx):
    """Une clé non-admin ne peut pas ingérer pour un autre compte (403)."""
    import io

    app, Sess = app_ctx
    from alambic_core.models import ApiKey, Config
    from alambic_core.services.api_keys import generate_key

    gen = generate_key()
    with Sess() as s:
        # Clé liée au compte acc1, config sur acc2.
        s.add(ApiKey(id="k1", apikey_name="Scoped", key_hash=gen.key_hash,
                     key_prefix=gen.key_prefix, is_admin=False,
                     account_id="acc1", is_active=True))
        s.add(Config(id="cfg2", config_name="Autre", account_id="acc2"))
        s.commit()

    client = app.test_client()
    resp = client.post(
        "/api/v1/ingest",
        headers={"Authorization": f"Bearer {gen.plaintext}"},
        data={"config_id": "cfg2", "file": (io.BytesIO(b"x"), "d.pdf")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403
