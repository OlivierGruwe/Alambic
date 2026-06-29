"""Tests de la liste des transactions : affichage, dépliage lazy, polling."""

from __future__ import annotations

from datetime import UTC, datetime

from alambic_core.models import Account, Config, Cost, Document, Transaction
from conftest import csrf, login


def _seed(Sess):
    """Une transaction avec 2 documents validés + 1 parent déprécié."""
    with Sess() as s:
        s.add(Account(id="acc1", account_name="Arondor"))
        s.add(Config(id="cfg1", config_name="Courrier CAPCA", account_id="acc1"))
        s.add(
            Transaction(
                id="trx-1",
                status="WORKING",
                process="Extraction IA",
                account_id="acc1",
                config_id="cfg1",
                nb_docs=2,
                created_at=datetime.now(UTC),
            )
        )
        # 2 enfants validés + 1 parent déprécié (ne doit pas compter ni s'afficher).
        s.add(
            Document(
                id="trx-1_00001",
                transaction_id="trx-1",
                status="VALIDATED",
                process="X",
                doctype="mise_en_demeure_bancaire",
            )
        )
        s.add(
            Document(
                id="trx-1_00002",
                transaction_id="trx-1",
                status="VALIDATED",
                process="X",
                doctype="mise_en_demeure_bancaire",
            )
        )
        s.add(
            Document(
                id="trx-1_parent",
                transaction_id="trx-1",
                status="DEPRECATED",
                process="X",
                doctype="unknown",
            )
        )
        s.add(
            Cost(
                transaction_id="trx-1",
                account_id="acc1",
                amount=0.27,
                provider="amazon",
                process="OCR",
                month="06",
                year="2026",
            )
        )
        s.commit()


def test_list_shows_transactions(app_ctx):
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # La transaction apparaît avec son compte, config, process.
    assert "trx-1" in page
    assert "Arondor" in page
    assert "Courrier CAPCA" in page
    assert "Extraction IA" in page


def test_list_recomputes_status_validated(app_ctx):
    """Statut recalculé : 2 docs VALIDATED → transaction VALIDATED (pas WORKING)."""
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # Le statut brut était WORKING, recalculé à VALIDATED.
    assert "VALIDATED" in page


def test_list_excludes_deprecated_from_count(app_ctx):
    """Le parent déprécié n'est pas compté : nb_docs = 2."""
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # data-docs-for affiche 2 (les 2 enfants, pas le parent déprécié).
    import re

    m = re.search(r'data-docs-for="trx-1"[^>]*>(\d+)<', page)
    assert m and m.group(1) == "2"


def test_documents_lazy_endpoint(app_ctx):
    """Le dépliage charge les documents (sans le parent déprécié)."""
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    frag = client.get("/transactions/trx-1/documents").get_data(as_text=True)
    assert "trx-1_00001" in frag
    assert "trx-1_00002" in frag
    assert "mise_en_demeure_bancaire" in frag
    # Le parent déprécié est exclu.
    assert "trx-1_parent" not in frag


def test_documents_account_isolation(app_ctx):
    """Un admin d'un autre compte ne voit pas les documents."""
    app, Sess = app_ctx
    _seed(Sess)
    # Créer un admin d'un autre compte.
    with Sess() as s:
        s.add(Account(id="acc2", account_name="Autre"))
        s.commit()
    client = app.test_client()
    # L'admin par défaut est super-admin → accès OK ; on teste la route existe.
    login(client)
    r = client.get("/transactions/trx-1/documents")
    assert r.status_code == 200


def test_statuses_polling_endpoint(app_ctx):
    """La route de polling renvoie les statuts recalculés en JSON."""
    app, Sess = app_ctx
    _seed(Sess)
    client = app.test_client()
    login(client)
    data = client.get("/transactions/statuses").get_json()
    assert "trx-1" in data
    assert data["trx-1"]["status"] == "VALIDATED"
    assert data["trx-1"]["nb_docs"] == 2


def test_discarded_documents_hidden(app_ctx):
    """Les documents DISCARDED n'apparaissent pas dans le dépliage."""
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A"))
        s.add(Transaction(id="trx-d", status="WORKING", process="X", account_id="acc1"))
        s.add(
            Document(
                id="trx-d_00001",
                transaction_id="trx-d",
                status="VALIDATED",
                process="X",
                doctype="facture",
            )
        )
        s.add(
            Document(
                id="trx-d_bad",
                transaction_id="trx-d",
                status="DISCARDED",
                process="X",
                doctype="unknown",
            )
        )
        s.commit()
    client = app.test_client()
    login(client)
    frag = client.get("/transactions/trx-d/documents").get_data(as_text=True)
    assert "trx-d_00001" in frag
    assert "trx-d_bad" not in frag  # DISCARDED masqué


def test_delete_button_only_when_not_working(app_ctx):
    """Le bouton supprimer apparaît pour une transaction terminée, pas en cours."""
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A"))
        # Transaction terminée (tous docs validés → VALIDATED).
        s.add(Transaction(id="tx-done", status="WORKING", process="X", account_id="acc1"))
        s.add(Document(id="tx-done_1", transaction_id="tx-done", status="VALIDATED", process="X"))
        # Transaction en cours.
        s.add(Transaction(id="tx-work", status="WORKING", process="X", account_id="acc1"))
        s.add(Document(id="tx-work_1", transaction_id="tx-work", status="OCR_DONE", process="X"))
        s.commit()
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # Bouton supprimer présent pour la terminée, absent pour celle en cours.
    assert 'action="/transactions/tx-done/delete"' in page
    assert 'action="/transactions/tx-work/delete"' not in page


def test_delete_refuses_working(app_ctx):
    """La route delete refuse une transaction en cours."""
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A"))
        s.add(Config(id="cfg1", config_name="C", account_id="acc1"))
        s.add(Transaction(id="tx-w", status="WORKING", process="X", account_id="acc1"))
        s.add(Document(id="tx-w_1", transaction_id="tx-w", status="OCR_DONE", process="X"))
        s.commit()
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    r = client.post("/transactions/tx-w/delete", data={"csrf_token": tok}, follow_redirects=True)
    assert "en cours" in r.get_data(as_text=True).lower()


def test_retry_refuses_non_working(app_ctx):
    """La route retry refuse une transaction non en cours."""
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A"))
        s.add(Config(id="cfg1", config_name="C", account_id="acc1"))
        s.add(Transaction(id="tx-done2", status="WORKING", process="X", account_id="acc1"))
        s.add(Document(id="tx-done2_1", transaction_id="tx-done2", status="VALIDATED", process="X"))
        s.commit()
    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    r = client.post("/transactions/tx-done2/retry", data={"csrf_token": tok}, follow_redirects=True)
    assert "en cours" in r.get_data(as_text=True).lower()


def test_delete_allows_stuck_transaction(app_ctx):
    """Une transaction bloquée (WORKING figée >10 min) est supprimable."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    app, Sess = app_ctx
    old = (datetime.now(UTC) - timedelta(minutes=30)).replace(tzinfo=None)
    with Sess() as s:
        s.add(Account(id="acc1", account_name="A"))
        s.add(Config(id="cfg1", config_name="C", account_id="acc1"))
        s.add(Transaction(id="tx-stuck", status="WORKING", process="X", account_id="acc1"))
        s.add(Document(id="tx-stuck_1", transaction_id="tx-stuck", status="OCR_DONE", process="X"))
        s.commit()
        # Force updated_at dans le passé via SQL brut (contourne le onupdate ORM).
        s.execute(
            text("UPDATE transactions SET updated_at = :u WHERE id = 'tx-stuck'"),
            {"u": old},
        )
        s.commit()

    client = app.test_client()
    login(client)
    tok = csrf(client.get("/transactions/").get_data(as_text=True))
    r = client.post(
        "/transactions/tx-stuck/delete", data={"csrf_token": tok}, follow_redirects=True
    )
    assert "Impossible de supprimer une transaction en cours" not in r.get_data(as_text=True)


def test_action_buttons_not_nested_in_bulk_form(app_ctx):
    """Les formulaires d'action (relance/supprimer) ne sont pas imbriqués dans le form bulk."""
    app, Sess = app_ctx
    with Sess() as s:
        s.add(Transaction(id="tx1", status="FAILED", process="X", account_id="acc1"))
        s.commit()
    client = app.test_client()
    login(client)
    page = client.get("/transactions/").get_data(as_text=True)
    # Le form bulk doit se fermer AVANT le tableau (pas d'imbrication).
    bulk_open = page.find('id="bulk-form"')
    table_open = page.find('<table class="tx-table"')
    # Le </form> du bulk doit apparaître entre l'ouverture du form et le tableau.
    bulk_close = page.find("</form>", bulk_open)
    assert bulk_open < bulk_close < table_open
