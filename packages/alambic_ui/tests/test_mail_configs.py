"""Tests des routes UI de gestion des boîtes mail (IMAP)."""

from __future__ import annotations

from conftest import csrf, login


def _create_payload(tok, **over):
    data = {
        "mailconfig_name": "Factures Fournisseurs",
        "email_address": "factures@arondor.com",
        "account_id": "",
        "config_id": "",
        "imap_server": "imap.arondor.com",
        "imap_port": "993",
        "imap_password": "SECRET_IMAP",
        "imap_inbox": "INBOX",
        "imap_search_criteria": "(UNSEEN)",
        "imap_alias": "",
        "content_mode": "all",
        "filter_attachment_extensions": ".pdf,.docx",
        "sender_whitelist": "*@arondor.com",
        "after_process_action": "seen",
        "after_process_folder": "ARCHIVE",
        "csrf_token": tok,
    }
    data.update(over)
    return data


def test_mail_configs_requires_admin(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")  # validateur, pas admin
    r = client.get("/mail-configs/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()


def test_create_mail_config_normalizes_name_and_encrypts(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/mail-configs/new").get_data(as_text=True))
    r = client.post("/mail-configs/new", data=_create_payload(tok), follow_redirects=True)
    assert r.status_code == 200

    from alambic_core.models import MailConfig

    with Sess() as s:
        mc = s.query(MailConfig).first()
        assert mc is not None
        # Nom normalisé en snake_case.
        assert mc.mailconfig_name == "factures_fournisseurs"
        assert mc.email_address == "factures@arondor.com"
        # Mot de passe déchiffré à la lecture (chiffré au repos).
        assert mc.imap_password_enc == "SECRET_IMAP"
        assert mc.content_mode == "all"
        assert mc.filter_attachment_extensions == ".pdf,.docx"


def test_edit_preserves_password_when_blank(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/mail-configs/new").get_data(as_text=True))
    client.post("/mail-configs/new", data=_create_payload(tok), follow_redirects=True)

    from alambic_core.models import MailConfig

    with Sess() as s:
        mc_id = s.query(MailConfig).first().id

    # Édition SANS re-saisir le mot de passe (champ vide) → secret conservé.
    tok2 = csrf(client.get(f"/mail-configs/{mc_id}/edit").get_data(as_text=True))
    client.post(
        f"/mail-configs/{mc_id}/edit",
        data=_create_payload(tok2, imap_password="", mailconfig_name="Factures MAJ"),
        follow_redirects=True,
    )
    with Sess() as s:
        mc = s.get(MailConfig, mc_id)
        assert mc.mailconfig_name == "factures_maj"
        assert mc.imap_password_enc == "SECRET_IMAP"  # inchangé


def test_edit_changes_password_when_provided(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/mail-configs/new").get_data(as_text=True))
    client.post("/mail-configs/new", data=_create_payload(tok), follow_redirects=True)

    from alambic_core.models import MailConfig

    with Sess() as s:
        mc_id = s.query(MailConfig).first().id

    tok2 = csrf(client.get(f"/mail-configs/{mc_id}/edit").get_data(as_text=True))
    client.post(
        f"/mail-configs/{mc_id}/edit",
        data=_create_payload(tok2, imap_password="NOUVEAU_SECRET"),
        follow_redirects=True,
    )
    with Sess() as s:
        mc = s.get(MailConfig, mc_id)
        assert mc.imap_password_enc == "NOUVEAU_SECRET"


def test_delete_mail_config(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/mail-configs/new").get_data(as_text=True))
    client.post("/mail-configs/new", data=_create_payload(tok), follow_redirects=True)

    from alambic_core.models import MailConfig

    with Sess() as s:
        mc_id = s.query(MailConfig).first().id

    tok_del = csrf(client.get("/mail-configs/").get_data(as_text=True))
    r = client.post(
        f"/mail-configs/{mc_id}/delete",
        data={"csrf_token": tok_del},
        follow_redirects=True,
    )
    assert r.status_code == 200
    with Sess() as s:
        assert s.get(MailConfig, mc_id) is None
