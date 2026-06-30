"""Tests d'intégration de l'interface : authentification + CRUD accounts."""

from __future__ import annotations

from alambic_core.models import Account
from conftest import csrf, login


def test_protected_route_redirects_to_login(app_ctx):
    app, _ = app_ctx
    r = app.test_client().get("/accounts/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.location


def test_login_wrong_password(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    r = login(client, password="faux")
    assert "incorrect" in r.get_data(as_text=True).lower()


def test_login_success_shows_accounts(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    r = login(client)
    assert r.status_code == 200
    assert "Comptes" in r.get_data(as_text=True)


def test_create_account(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/accounts/new").get_data(as_text=True))
    client.post(
        "/accounts/new",
        data={
            "account_name": "ACME",
            "town": "Bordeaux",
            "country": "France",
            "active": "y",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        accs = s.query(Account).all()
        assert len(accs) == 1
        assert accs[0].account_name == "acme"
        assert accs[0].town == "Bordeaux"


def test_edit_account_keeps_active(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        s.add(Account(id="a1", account_name="Old", active=True))
        s.commit()
    editp = client.get("/accounts/a1/edit").get_data(as_text=True)
    tok = csrf(editp)
    # active="y" simule la checkbox cochée (rendue cochée pour un compte actif)
    client.post(
        "/accounts/a1/edit",
        data={
            "account_name": "New",
            "town": "Paris",
            "country": "FR",
            "active": "y",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        acc = s.get(Account, "a1")
        assert acc.account_name == "new"
        assert acc.active is True


def test_toggle_account(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        s.add(Account(id="a1", account_name="X", active=True))
        s.commit()
    tok = csrf(client.get("/accounts/").get_data(as_text=True))
    client.post("/accounts/a1/toggle", data={"csrf_token": tok}, follow_redirects=True)
    with Sess() as s:
        assert s.get(Account, "a1").active is False


def test_validator_cannot_access_accounts(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")
    r = client.get("/accounts/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()


def test_logout(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    r = client.get("/logout", follow_redirects=True)
    assert "connecter" in r.get_data(as_text=True).lower()


def test_create_account_with_address_and_secret(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/accounts/new").get_data(as_text=True))
    client.post(
        "/accounts/new",
        data={
            "account_name": "ACME",
            "active": "y",
            "address1": "10 rue de la Paix",
            "address3": "BP 42",
            "zip": "33000",
            "town": "Bordeaux",
            "country": "France",
            "enrich_allowed_domains": "api.partenaire.com, api.autre.fr",
            "edenai_secret_key": "SECRET-EDENAI-123",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        acc = s.query(Account).filter_by(account_name="acme").first()
        assert acc.address == {"line1": "10 rue de la Paix", "line3": "BP 42"}
        assert acc.enrich_allowed_domains == "api.partenaire.com, api.autre.fr"
        # Le secret est stocké (chiffré, mais lisible via le provider)
        assert acc.edenai_secret_key == "SECRET-EDENAI-123"


def test_edit_empty_secret_keeps_existing(app_ctx):
    """Cas critique : éditer sans toucher au secret ne l'efface pas."""
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        s.add(Account(id="a1", account_name="X", active=True, edenai_secret_key="ORIGINAL-SECRET"))
        s.commit()
    editp = client.get("/accounts/a1/edit").get_data(as_text=True)
    # Le secret ne doit PAS apparaître dans le HTML
    assert "ORIGINAL-SECRET" not in editp
    tok = csrf(editp)
    # On édite le nom mais on laisse le secret vide
    client.post(
        "/accounts/a1/edit",
        data={
            "account_name": "X renamed",
            "active": "y",
            "edenai_secret_key": "",  # vide → inchangé
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        acc = s.get(Account, "a1")
        assert acc.account_name == "x_renamed"
        assert acc.edenai_secret_key == "ORIGINAL-SECRET"  # conservé !


def test_edit_new_secret_replaces(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        s.add(Account(id="a1", account_name="X", active=True, edenai_secret_key="OLD"))
        s.commit()
    tok = csrf(client.get("/accounts/a1/edit").get_data(as_text=True))
    client.post(
        "/accounts/a1/edit",
        data={
            "account_name": "X",
            "active": "y",
            "edenai_secret_key": "NEW-SECRET",  # saisi → remplace
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    with Sess() as s:
        assert s.get(Account, "a1").edenai_secret_key == "NEW-SECRET"
