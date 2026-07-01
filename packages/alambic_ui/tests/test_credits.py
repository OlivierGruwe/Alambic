"""Tests de l'affichage des crédits EdenAI (fiche compte + dashboard)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from conftest import login


def _seed_account_with_costs(Sess):
    from alambic_core.models import Account, Cost

    now = datetime.now(UTC)
    with Sess() as s:
        s.add(Account(id="acc1", account_name="arondor",
                      edenai_secret_key=json.dumps({"secret_key": "fake_jwt"})))
        for i in range(7):
            s.add(Cost(id=f"c{i}", account_id="acc1", amount=2.0, process="OCR",
                       created_at=now - timedelta(days=i)))
        s.commit()


def test_account_edit_shows_credits(app_ctx):
    app, Sess = app_ctx
    _seed_account_with_costs(Sess)
    from alambic_core.ai.edenai_credits import clear_cache

    clear_cache()
    client = app.test_client()
    login(client)
    mock = MagicMock(status_code=200)
    mock.json.return_value = {"credits": 100.0}
    with patch("requests.get", return_value=mock):
        page = client.get("/accounts/acc1/edit").get_data(as_text=True)
    assert "100.00" in page
    assert "Autonomie estimée" in page
    assert "Crédits EdenAI" in page


def test_account_edit_credits_unavailable(app_ctx):
    app, Sess = app_ctx
    _seed_account_with_costs(Sess)
    from alambic_core.ai.edenai_credits import clear_cache

    clear_cache()
    client = app.test_client()
    login(client)
    mock = MagicMock(status_code=401)
    with patch("requests.get", return_value=mock):
        page = client.get("/accounts/acc1/edit").get_data(as_text=True)
    assert "indisponible" in page.lower()


def test_dashboard_shows_credits_table(app_ctx):
    app, Sess = app_ctx
    _seed_account_with_costs(Sess)
    from alambic_core.ai.edenai_credits import clear_cache

    clear_cache()
    client = app.test_client()
    login(client)
    mock = MagicMock(status_code=200)
    mock.json.return_value = {"credits": 100.0}
    with patch("requests.get", return_value=mock):
        page = client.get("/dashboard/").get_data(as_text=True)
    assert "Crédits EdenAI" in page or "autonomie" in page.lower()
    assert "arondor" in page
