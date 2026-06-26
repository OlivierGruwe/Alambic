"""
Test d'intégration NIVEAU 2 — dépôt + création de transaction de bout en bout.

Tape sur l'infra docker-compose RÉELLE : PostgreSQL (port 5433) ET Garage.
Contrairement aux tests unitaires (SQLite + mocks), celui-ci valide :
  - que le double upload atterrit vraiment dans les buckets Garage,
  - que la transaction est créée dans le vrai PostgreSQL (schéma, contraintes),
  - que l'idempotence par transaction_key fonctionne en conditions réelles.

PRÉREQUIS (sinon le test est SKIP) :
  - docker compose up : Postgres + Garage debout,
  - buckets initialisés (make garage-init),
  - migration appliquée (alembic upgrade head), dont l'index unique partiel,
  - variables d'env chargées (.env) :
      ALAMBIC_DATABASE_URL=postgresql+psycopg://...@localhost:5433/alambic
      ALAMBIC_SECRET_KEY=<clé Fernet>
      ALAMBIC_S3_ENDPOINT=http://localhost:3900
      ALAMBIC_S3_ACCESS_KEY=<clé applicative Garage>
      ALAMBIC_S3_SECRET_KEY=<secret applicatif Garage>
      ALAMBIC_S3_WORK_BUCKET=alambic-work
      ALAMBIC_S3_INPUT_BUCKET=alambic-input

Lancement : uv run pytest -m integration packages/alambic_workers/tests/ -v

Le test nettoie derrière lui (objets Garage + lignes Postgres créés).
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration


def _env_ready() -> bool:
    """Vrai si toutes les variables d'infra sont présentes."""
    required = [
        "ALAMBIC_DATABASE_URL",
        "ALAMBIC_SECRET_KEY",
        "ALAMBIC_S3_ENDPOINT",
        "ALAMBIC_S3_ACCESS_KEY",
        "ALAMBIC_S3_SECRET_KEY",
    ]
    return all(os.environ.get(v) for v in required)


requires_infra = pytest.mark.skipif(
    not _env_ready(),
    reason="Infra docker-compose requise (Postgres + Garage) + variables .env",
)


@pytest.fixture
def infra():
    """Initialise alambic_core sur le vrai Postgres et fournit les helpers."""
    from alambic_core.db.session import get_sessionmaker, init_core

    init_core()  # lit ALAMBIC_DATABASE_URL + ALAMBIC_SECRET_KEY
    Sess = get_sessionmaker()
    return Sess


@requires_infra
def test_full_deposit_creates_transaction_and_uploads(infra, tmp_path):
    """Dépôt direct → 2 objets dans Garage + transaction WORKING en Postgres."""
    from alambic_core.models import Account, Config, Transaction

    from alambic_workers import storage
    from alambic_workers.tasks import ingestion, start_ingestion

    Sess = infra
    s3 = storage.get_s3_client()
    work_bucket = os.environ.get("ALAMBIC_S3_WORK_BUCKET", "alambic-work")
    input_bucket = os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input")

    # Identifiants uniques pour ce run (évite les collisions entre exécutions)
    run = uuid.uuid4().hex[:8]
    acc_id = f"Acc_test_{run}"
    cfg_id = f"Con_test_{run}"
    object_key = f"__uploads__/{acc_id}/{cfg_id}/UI_IMPORT/facture_{run}.pdf"

    # Fichier local à déposer
    local = tmp_path / "facture.pdf"
    local.write_bytes(b"%PDF-1.4 fake content")

    created_tx_ids: list[str] = []
    uploaded_keys: list[tuple[str, str]] = []

    try:
        # Prépare account + config dans le vrai Postgres
        with Sess() as s:
            s.add(Account(id=acc_id, account_name="ACME test"))
            s.add(Config(id=cfg_id, account_id=acc_id, config_name="cfg test"))
            s.commit()

        # ── DÉPÔT : déclenche le workflow (Celery mocké, on teste upload + DB) ──
        from unittest.mock import patch

        with patch.object(start_ingestion.app, "signature") as mock_sig:
            result = start_ingestion.start_ingestion(
                bucket=input_bucket,
                object_key=object_key,
                local_path=str(local),
                author="olivier@test",
            )

        assert result is not None, "le dépôt aurait dû lancer le workflow"
        tx_id = result["transactionId"]
        created_tx_ids.append(tx_id)

        # ── Vérif 1 : les 2 objets sont bien dans Garage ────────────────────
        work_key = f"__transactions__/{acc_id}/{cfg_id}/{tx_id}/{tx_id}.pdf"
        backup_key = object_key.replace("__uploads__", "__backup__")
        uploaded_keys = [(work_bucket, work_key), (input_bucket, backup_key)]

        head_work = s3.head_object(Bucket=work_bucket, Key=work_key)
        assert head_work["ContentLength"] > 0
        head_backup = s3.head_object(Bucket=input_bucket, Key=backup_key)
        assert head_backup["ContentLength"] > 0

        # ── Le payload passé à Celery contient bien le transaction_key ───────
        call_args = mock_sig.call_args
        payload = call_args.kwargs.get(
            "args", call_args.args[1] if len(call_args.args) > 1 else None
        )
        payload = payload[0] if isinstance(payload, list) else payload
        assert payload["transaction"]["transaction_key"]
        tkey = payload["transaction"]["transaction_key"]

        # ── On exécute create_transaction (l'étape qui persiste la transaction) ─
        ingestion.create_transaction(payload)

        # ── Vérif 2 : la transaction existe en Postgres avec sa clé ──────────
        with Sess() as s:
            tx = s.get(Transaction, tx_id)
            assert tx is not None
            assert tx.status == "WORKING"
            assert tx.transaction_key == tkey

        # ── Vérif 3 : idempotence — 2e dépôt même fichier → skip ─────────────
        with patch.object(start_ingestion.app, "signature") as mock_sig2:
            result2 = start_ingestion.start_ingestion(
                bucket=input_bucket,
                object_key=object_key,
                local_path=str(local),
            )
        assert result2 is None, "le 2e dépôt aurait dû être skippé (idempotence)"
        assert not mock_sig2.called

    finally:
        # ── Nettoyage : objets Garage + lignes Postgres ─────────────────────
        import contextlib

        for bucket, key in uploaded_keys:
            with contextlib.suppress(Exception):
                s3.delete_object(Bucket=bucket, Key=key)
        with Sess() as s:
            from alambic_core.models import Account, Config

            for tx_id in created_tx_ids:
                tx = s.get(Transaction, tx_id)
                if tx:
                    s.delete(tx)
            cfg = s.get(Config, cfg_id)
            if cfg:
                s.delete(cfg)
            acc = s.get(Account, acc_id)
            if acc:
                s.delete(acc)
            s.commit()
