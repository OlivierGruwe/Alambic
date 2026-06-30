"""
Tests de l'orchestrateur d'ingestion — logique pure (intrinsics ASL traduits).

Ne teste PAS le workflow Celery complet (qui nécessite broker + DB) mais les
briques déterministes : la préparation du document (StringSplit/Format) et le
filtre des métadonnées (FilterEmptyMetadata). Rapides, sans Docker ni broker.
"""

from __future__ import annotations

from alambic_workers.orchestration.ingestion import (
    _prepare_document,
    _write_metadata_indexes,
)


def test_prepare_document_translates_intrinsics():
    """trx-XXX → doc-XXX (States.StringSplit + States.Format)."""
    payload = {
        "transaction": {"transactionId": "trx-abc123"},
        "documents": [{"file": {"bucket": "alambic-input", "key": "facture.pdf"}}],
    }
    out = _prepare_document(payload)
    assert out["transactionId"] == "trx-abc123"
    assert out["document"]["documentId"] == "doc-abc123"
    assert out["document"]["file"]["key"] == "facture.pdf"


class _FakeRepo:
    def __init__(self):
        self.calls = []

    def put_metadata_index(self, document_id, name, value):
        self.calls.append((name, value))


def test_write_metadata_indexes_filters_empty():
    """Le filtre FilterEmptyMetadata : name et value doivent être non vides."""
    repo = _FakeRepo()
    payload = {
        "document": {"documentId": "doc-1"},
        "datas": [
            {"name": "client", "value": "ACME"},
            {"name": "", "value": "x"},
            {"name": "montant", "value": ""},
            {"name": "date", "value": "2026-01-01"},
        ],
    }
    _write_metadata_indexes(repo, payload)
    assert repo.calls == [("client", "ACME"), ("date", "2026-01-01")]


def test_write_metadata_indexes_empty_datas():
    """Aucune métadonnée → aucun appel (pas d'erreur)."""
    repo = _FakeRepo()
    _write_metadata_indexes(repo, {"document": {"documentId": "doc-1"}})
    assert repo.calls == []
