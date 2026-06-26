"""Tests du CRUD utilisateurs et du flux d'invitation."""

from __future__ import annotations

from alambic_core.models import User
from conftest import csrf, login


def _create_invited_user(client, email="newuser@arondor.com", role="VALIDATOR"):
    tok = csrf(client.get("/users/new").get_data(as_text=True))
    return client.post(
        "/users/new",
        data={
            "email": email,
            "full_name": "New User",
            "role": role,
            "account_id": "",
            "active": "y",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )


def test_admin_required_for_users(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")  # validateur
    r = client.get("/users/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()


def test_create_user_generates_invite(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    r = _create_invited_user(client)
    body = r.get_data(as_text=True)
    assert "invitation" in body.lower()
    assert "/invitation/" in body  # le lien est affiché
    with Sess() as s:
        u = s.query(User).filter_by(email="newuser@arondor.com").first()
        assert u is not None
        assert u.invite_token is not None
        assert u.password_hash == ""  # pas encore de mot de passe


def test_accept_invitation_sets_password(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    _create_invited_user(client)
    with Sess() as s:
        u = s.query(User).filter_by(email="newuser@arondor.com").first()
        token = u.invite_token

    # Page publique d'invitation (sans login) — nouveau client
    pub = app.test_client()
    page = pub.get(f"/invitation/{token}").get_data(as_text=True)
    assert "mot de passe" in page.lower()
    tok = csrf(page)
    pub.post(
        f"/invitation/{token}",
        data={"password": "MonMdp1234", "confirm": "MonMdp1234", "csrf_token": tok},
        follow_redirects=True,
    )
    with Sess() as s:
        u = s.query(User).filter_by(email="newuser@arondor.com").first()
        assert u.password_hash != ""  # mot de passe défini
        assert u.invite_token is None  # jeton consommé

    # L'utilisateur peut maintenant se connecter
    r = login(pub, email="newuser@arondor.com", password="MonMdp1234")
    assert r.status_code == 200


def test_invalid_invitation_token(app_ctx):
    app, _ = app_ctx
    r = app.test_client().get("/invitation/inexistant")
    assert r.status_code == 404


def test_admin_cannot_create_super_admin(app_ctx):
    app, Sess = app_ctx
    # Créer un admin rattaché à un compte
    from alambic_core.domain.enums import UserRole
    from alambic_core.models import Account
    from alambic_core.security.passwords import hash_password

    with Sess() as s:
        s.add(Account(id="acc1", account_name="ACME"))
        s.add(
            User(
                id="adm",
                email="admin2@x.fr",
                role=UserRole.ADMIN.value,
                account_id="acc1",
                password_hash=hash_password("p"),
                active=True,
            )
        )
        s.commit()
    client = app.test_client()
    login(client, email="admin2@x.fr", password="p")
    tok = csrf(client.get("/users/new").get_data(as_text=True))
    r = client.post(
        "/users/new",
        data={
            "email": "super@x.fr",
            "full_name": "",
            "role": "SUPER_ADMIN",
            "account_id": "",
            "active": "y",
            "csrf_token": tok,
        },
        follow_redirects=True,
    )
    assert "ne pouvez pas attribuer" in r.get_data(as_text=True).lower()


def test_delete_user(app_ctx):
    app, Sess = app_ctx
    client = app.test_client()
    login(client)
    with Sess() as s:
        from alambic_core.domain.enums import UserRole

        s.add(User(id="todel", email="del@x.fr", role=UserRole.VALIDATOR.value, active=True))
        s.commit()
    tok = csrf(client.get("/users/").get_data(as_text=True))
    client.post("/users/todel/delete", data={"csrf_token": tok}, follow_redirects=True)
    with Sess() as s:
        assert s.get(User, "todel") is None
