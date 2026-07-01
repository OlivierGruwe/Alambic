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


def test_dashboard_period_selector(app_ctx):
    """Le sélecteur de période affiche les 3 fenêtres et accepte le paramètre."""
    app, _ = app_ctx
    client = app.test_client()
    login(client)

    # Les trois périodes sont proposées.
    page = client.get("/dashboard/").get_data(as_text=True)
    assert "7 derniers jours" in page
    assert "30 derniers jours" in page
    assert "12 derniers mois" in page

    # Chaque période est acceptée et rend la page.
    for period in ("week", "month", "year"):
        r = client.get(f"/dashboard/?period={period}")
        assert r.status_code == 200

    # Une période invalide retombe sur le défaut sans erreur.
    assert client.get("/dashboard/?period=bogus").status_code == 200


def test_classification_breakdown(app_ctx):
    """Le dashboard affiche la répartition des 3 nœuds de classification."""
    from datetime import datetime

    from alambic_core.models import Cost

    app, Sess = app_ctx
    now = datetime.now()
    with Sess() as s:
        # 2 gratuites (vectoriel + lexical), 1 payante (LLM)
        s.add(Cost(amount=0, process="CLASSIFY", source="embedding_v3",
                   document_id="d1", month=f"{now.month:02d}", year=str(now.year)))
        s.add(Cost(amount=0, process="CLASSIFY", source="lexical_v2",
                   document_id="d2", month=f"{now.month:02d}", year=str(now.year)))
        s.add(Cost(amount=0.255, process="CLASSIFY", source="llm_vbootstrap",
                   provider="mistral", document_id="d3",
                   month=f"{now.month:02d}", year=str(now.year)))
        s.commit()

    client = app.test_client()
    login(client)
    html = client.get("/dashboard/").get_data(as_text=True)
    # Le panneau de répartition est présent.
    assert "Répartition de la classification" in html
    # La part gratuite (2/3 ≈ 67 %) est mise en avant.
    assert "67 %" in html or "gratuite" in html


def test_projection_floor_and_warning(app_ctx):
    """La projection expose un plancher 'à maturité' et avertit si bootstrap."""
    from datetime import datetime

    from alambic_core.models import Cost

    app, Sess = app_ctx
    now = datetime.now()
    with Sess() as s:
        # Coût dominé par le LLM → avertissement attendu.
        s.add(Cost(amount=0.30, process="CLASSIFY", source="llm_vbootstrap",
                   provider="mistral", document_id="d1",
                   month=f"{now.month:02d}", year=str(now.year)))
        s.add(Cost(amount=0.01, process="OCR", source="", provider="mistral",
                   document_id="d1", month=f"{now.month:02d}", year=str(now.year)))
        s.commit()

    client = app.test_client()
    login(client)
    html = client.get("/dashboard/").get_data(as_text=True)
    assert "À maturité" in html or "maturité" in html
    # Avertissement bootstrap (part LLM élevée).
    assert "Estimation haute" in html


def test_recent_activity_shows_account_and_config(app_ctx):
    """L'activité récente affiche les colonnes Compte et Config (par nom)."""
    from alambic_core.models import Account, Config, Transaction

    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="Arondor"))
        s.add(Config(id="cfg1", config_name="auto", account_id="acc1"))
        s.add(Transaction(id="tx-recent", status="WORKING", process="OCR",
                          nb_docs=2, account_id="acc1", config_id="cfg1"))
        s.commit()

    client = app.test_client()
    login(client)
    page = client.get("/dashboard/").get_data(as_text=True)
    # En-têtes de colonnes présents.
    assert "Compte" in page
    assert "Config" in page
    # Les noms (pas les ids) sont affichés.
    assert "Arondor" in page
    assert "auto" in page


def test_recent_activity_shows_origin(app_ctx):
    """L'activité récente affiche l'origine (canal) des transactions."""
    from alambic_core.models import Transaction

    app, Sess = app_ctx
    with Sess() as s:
        s.add(Transaction(id="tx-m", status="WORKING", process="OCR", origin="MAIL", nb_docs=1))
        s.add(Transaction(id="tx-w", status="WORKING", process="OCR", origin="WS", nb_docs=1))
        s.commit()

    client = app.test_client()
    login(client)
    page = client.get("/dashboard/").get_data(as_text=True)
    assert "Origine" in page
    assert "Mail" in page
    assert "Web service" in page
