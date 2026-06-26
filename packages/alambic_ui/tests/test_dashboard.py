"""Tests du blueprint dashboard (tableau de bord de supervision)."""

from __future__ import annotations

from datetime import UTC, datetime

from alambic_core.models import Cost, Document, Transaction, TransactionStep
from conftest import login


def test_dashboard_loads(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    r = client.get("/dashboard/")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Tableau de bord" in page


def test_home_redirects_to_dashboard(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 308)
    assert "/dashboard" in r.headers.get("Location", "")


def test_dashboard_shows_metrics(app_ctx):
    app, Sess = app_ctx
    now = datetime.now(UTC)
    with Sess() as s:
        s.add(Transaction(id="tx1", status="EXPORTED", process="EXPORT", nb_docs=2))
        s.add(Document(id="d1", transaction_id="tx1", status="EXPORTED", process="X"))
        s.add(
            Cost(
                transaction_id="tx1",
                amount=0.27,
                provider="amazon",
                process="OCR",
                month="06",
                year="2026",
            )
        )
        s.add(
            TransactionStep(
                transaction_id="tx1",
                process="OCR_READER",
                status="OK",
                started_at=now,
                duration_ms=162000,
            )
        )
        s.commit()

    client = app.test_client()
    login(client)
    page = client.get("/dashboard/").get_data(as_text=True)

    # Volumes, coûts, étapes apparaissent.
    assert "EXPORTED" in page
    assert "OCR" in page
    assert "amazon" in page
    assert "OCR_READER" in page


def test_dashboard_requires_admin(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client, email="val@arondor.com")  # validateur, pas admin
    r = client.get("/dashboard/", follow_redirects=True)
    assert "administrateurs" in r.get_data(as_text=True).lower()
