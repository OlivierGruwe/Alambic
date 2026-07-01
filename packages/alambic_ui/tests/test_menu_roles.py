"""Le menu latéral respecte les rôles : Administration réservée aux admins."""

from __future__ import annotations

from conftest import login


def test_admin_sees_all_sections(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)  # super-admin
    page = client.get("/dashboard/").get_data(as_text=True)
    assert "Administration" in page
    assert "Tableau de bord" in page
    assert "Comptes" in page
    assert "Traitement" in page
    assert "Transactions" in page
    # Les clés API sont désormais un vrai lien (plus un placeholder « bientôt »).
    assert "Clés API" in page


def test_validator_no_admin_section(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")  # valideur, pas admin
    page = client.get("/transactions/", follow_redirects=True).get_data(as_text=True)
    # La section de navigation Administration (pas le sous-titre du logo) est absente.
    assert '<div class="nav-section">Administration</div>' not in page
    # Le dashboard (admin only) n'est pas listé dans le menu.
    assert "Tableau de bord" not in page
