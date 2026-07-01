"""Tests de la page benchmark de robustesse (UI)."""

from __future__ import annotations

from conftest import login


def test_bench_page_accessible(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    page = client.get("/bench/").get_data(as_text=True)
    assert "Benchmark" in page
    assert "Robustesse" in page


def test_bench_run_shows_results(app_ctx):
    app, _ = app_ctx
    client = app.test_client()
    login(client)
    # Mode rapide (sans cas lourds) pour un test rapide.
    page = client.get("/bench/?run=1").get_data(as_text=True)
    assert "empty_file" in page
    assert "not_a_pdf" in page
    # Verdict de synthèse présent.
    assert "dégrade proprement" in page or "crash" in page.lower()
