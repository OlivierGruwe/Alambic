"""
Tests du portage de start_workflow.py (déclencheur d'ingestion en dépôt direct).

Couvre la logique pure (parsing de clé, hash de transaction, extension) et le
flux complet : double upload Garage + déclenchement Celery + idempotence par
transaction_key. Garage (storage.put_object) et Celery (app.signature) sont
mockés pour isoler la logique. SQLite en mémoire, sans Docker.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import alambic_core.models  # noqa: F401
import pytest
from alambic_core.db.base import Base
from alambic_core.db.types import set_secret_provider
from alambic_core.models import Account, Config, Transaction
from alambic_core.security.fernet_provider import FernetSecretProvider
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import alambic_workers.tasks.start_ingestion as si
from alambic_workers.tasks.start_ingestion import (
    InvalidInputError,
    _get_extension,
    compute_transaction_key,
    parse_upload_key,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def _provider():
    set_secret_provider(FernetSecretProvider(Fernet.generate_key().decode()))


@pytest.fixture
def sessionmaker_fixture():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng)
    with Sess() as s:
        s.add(Account(id="Acc_1", account_name="ACME"))
        s.add(Config(id="Con_1", account_id="Acc_1", config_name="cfg"))
        s.commit()
    return Sess


# ── Logique pure ─────────────────────────────────────────────────────────────
def test_parse_upload_key_ok():
    acc, cfg, origin, fn = parse_upload_key("__uploads__/Acc_1/Con_1/UI_IMPORT/facture.pdf")
    assert (acc, cfg, origin, fn) == ("Acc_1", "Con_1", "UI_IMPORT", "facture.pdf")


def test_parse_upload_key_unknown_origin():
    _, _, origin, _ = parse_upload_key("__uploads__/A/C/BIZARRE/f.pdf")
    assert origin == "UNKNOWN"


def test_parse_upload_key_malformed():
    with pytest.raises(InvalidInputError):
        parse_upload_key("trop/court")


def test_compute_transaction_key_deterministic():
    assert compute_transaction_key("b", "k") == compute_transaction_key("b", "k")
    assert compute_transaction_key("b", "k") != compute_transaction_key("b", "x")
    assert len(compute_transaction_key("b", "k")) == 64


def test_get_extension():
    assert _get_extension("facture.pdf") == "pdf"
    assert _get_extension("sansext") == ""


# ── Flux complet ─────────────────────────────────────────────────────────────
def _patched(sessionmaker_obj, uploads, fake_run):
    @contextmanager
    def fake_scope():
        s = sessionmaker_obj()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def fake_put(bucket, key, path, meta=None):
        uploads.append((bucket, key))

    return (
        patch.object(si, "session_scope", fake_scope),
        patch.object(si.storage, "put_object", fake_put),
        patch.object(si.app, "signature", return_value=fake_run),
    )


def test_start_ingestion_triggers_workflow(sessionmaker_fixture, tmp_path):
    uploads = []
    fake_run = MagicMock()
    p1, p2, p3 = _patched(sessionmaker_fixture, uploads, fake_run)
    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"data")

    with p1, p2, p3:
        result = si.start_ingestion(
            bucket="alambic-input",
            object_key="__uploads__/Acc_1/Con_1/UI_IMPORT/facture.pdf",
            local_path=str(tmp_file),
            author="olivier",
        )

    assert result is not None
    assert result["transactionId"].startswith("trx-")
    assert len(uploads) == 2  # double upload work + backup
    assert fake_run.delay.called


def test_start_ingestion_is_idempotent(sessionmaker_fixture, tmp_path):
    key = "__uploads__/Acc_1/Con_1/UI_IMPORT/facture.pdf"
    tk = compute_transaction_key("alambic-input", key)
    # Pré-insère une transaction avec cette clé → doit être vue comme doublon.
    with sessionmaker_fixture() as s:
        s.add(Transaction(id="trx-existing", transaction_key=tk, status="WORKING"))
        s.commit()

    uploads = []
    fake_run = MagicMock()
    p1, p2, p3 = _patched(sessionmaker_fixture, uploads, fake_run)
    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"data")

    with p1, p2, p3:
        result = si.start_ingestion(
            bucket="alambic-input", object_key=key, local_path=str(tmp_file)
        )

    assert result is None  # skippé
    assert not fake_run.delay.called
    assert uploads == []  # pas d'upload non plus


def test_start_ingestion_rejects_unknown_config(sessionmaker_fixture, tmp_path):
    uploads = []
    fake_run = MagicMock()
    p1, p2, p3 = _patched(sessionmaker_fixture, uploads, fake_run)
    tmp_file = tmp_path / "upload.pdf"
    tmp_file.write_bytes(b"data")

    with p1, p2, p3:
        result = si.start_ingestion(
            bucket="alambic-input",
            object_key="__uploads__/Acc_1/Con_INCONNU/UI_IMPORT/f.pdf",
            local_path=str(tmp_file),
        )

    assert result is None  # rejeté
    assert not fake_run.delay.called
    # une transaction REJECTED a été tracée
    with sessionmaker_fixture() as s:
        rejected = s.query(Transaction).filter(Transaction.status == "REJECTED").all()
    assert len(rejected) == 1
