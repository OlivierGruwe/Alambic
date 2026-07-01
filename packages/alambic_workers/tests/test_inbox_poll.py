"""Tests de la tâche d'import FTP/S3 (inbox.poll) avec connecteur mocké."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
def sess():
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"  # noqa: SIM115, E501
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()
    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    Base.metadata.create_all(get_engine())
    from alambic_core.models import Config

    S = get_sessionmaker()
    with S() as s:
        s.add(Config(id="cfg1", config_name="ftp", account_id="acc1", is_active=True,
                     ws={"way_in": "FTP", "ftp_server_in": "ftp.x.com",
                         "ftp_remote_dir_in": "/in"},
                     ftp_in_enc='{"password":"x"}'))
        s.commit()
    return S


def _fake_connector(files):
    conn = MagicMock()
    conn.source_type = "FTP"
    conn.list_files.return_value = files
    conn.fetch.return_value = b"%PDF-fake"
    conn.move_to_treated.side_effect = lambda k, **kw: f"/in/treated/20260701/{k.split('/')[-1]}"
    return conn


def test_import_and_dedup(sess):
    import alambic_workers.tasks.inbox_poll as ib

    conn = _fake_connector(["/in/a.pdf", "/in/b.pdf"])
    with patch.object(ib, "connector_from_config", return_value=conn), \
         patch.object(ib, "start_ingestion"), \
         patch.object(ib.storage, "put_object"), \
         patch.object(ib.storage, "build_upload_key",
                      side_effect=lambda a, c, f, origin: f"__uploads__/{a}/{c}/{origin}/{f}"):
        r1 = ib.poll_inboxes()
        assert r1["ingested"] == 2
        assert r1["moved"] == 2

        # 2e passage immédiat : dédoublonnés.
        r2 = ib.poll_inboxes()
        assert r2["ingested"] == 0
        assert r2["skipped"] == 2


def test_move_failure_does_not_lose_file(sess):
    import alambic_workers.tasks.inbox_poll as ib

    conn = _fake_connector(["/in/a.pdf"])
    conn.move_to_treated.side_effect = RuntimeError("FTP move failed")
    with patch.object(ib, "connector_from_config", return_value=conn), \
         patch.object(ib, "start_ingestion"), \
         patch.object(ib.storage, "put_object"), \
         patch.object(ib.storage, "build_upload_key",
                      side_effect=lambda a, c, f, origin: f"k/{f}"):
        r = ib.poll_inboxes()
        # Ingéré malgré l'échec du déplacement ; le registre couvre le double-import.
        assert r["ingested"] == 1
        assert r["moved"] == 0


def test_config_without_input_skipped(sess):
    from alambic_core.db.session import get_sessionmaker
    from alambic_core.models import Config

    import alambic_workers.tasks.inbox_poll as ib

    S = get_sessionmaker()
    with S() as s:
        # Config sans entrée FTP/S3 : ignorée par le balayage.
        s.add(Config(id="cfg2", config_name="none", is_active=True, ws={}))
        s.commit()

    conn = _fake_connector([])
    with patch.object(ib, "connector_from_config", return_value=conn), \
         patch.object(ib, "start_ingestion"):
        r = ib.poll_inboxes()
        # Seule cfg1 (FTP) est comptée, cfg2 (sans entrée) exclue.
        assert r["configs"] == 1
