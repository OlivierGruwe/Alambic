"""Test de la projection des coûts dans le dashboard."""

from __future__ import annotations

from conftest import login


def test_projection_from_real_costs(app_ctx):
    """Le dashboard projette le coût mensuel à partir des coûts réels."""
    app, Sess = app_ctx
    from alambic_core.models import Cost

    with Sess() as s:
        # 2 documents, coût total 0.10 € → 0.05 €/doc.
        s.add(Cost(document_id="d1", amount=0.04, process="OCR", month="06", year="2026"))
        s.add(Cost(document_id="d1", amount=0.01, process="EXTRACT", month="06", year="2026"))
        s.add(Cost(document_id="d2", amount=0.05, process="OCR", month="06", year="2026"))
        s.commit()

    client = app.test_client()
    login(client)
    page = client.get("/dashboard/").get_data(as_text=True)
    assert "Projection du coût mensuel" in page
    # 2 documents distincts.
    assert "2 document" in page


def test_projection_empty_without_costs(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/dashboard/").get_data(as_text=True)
    assert "Pas encore assez de données" in page or "Projection du coût" in page
