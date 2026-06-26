"""Tests de la rétention (purge des transactions exportées après délai)."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from contextlib import suppress

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
def core_db(monkeypatch):
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()
    os.environ["ALAMBIC_RETENTION_DAYS"] = "30"

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    @event.listens_for(get_engine(), "connect")
    def _fk_on(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(get_engine())

    import alambic_core.services.deletion as de

    monkeypatch.setattr(de.storage, "delete_prefix", lambda b, p: 0)
    monkeypatch.setattr(de.storage, "work_bucket", lambda: "alambic-work")

    yield get_sessionmaker()
    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def _seed(Sess, now):
    from alambic_core.models import Account, Config, Transaction

    with Sess() as s:
        s.add(Account(id="acc1", account_name="A"))
        s.add(
            Config(id="c7", account_id="acc1", config_name="court", general={"retention_days": 7})
        )
        s.add(
            Config(id="c90", account_id="acc1", config_name="long", general={"retention_days": 90})
        )
        s.flush()
        s.add(
            Transaction(
                id="t1", status="EXPORTED", config_id="c7", exported_at=now - dt.timedelta(days=10)
            )
        )
        s.add(
            Transaction(
                id="t2", status="EXPORTED", config_id="c90", exported_at=now - dt.timedelta(days=10)
            )
        )
        s.add(
            Transaction(
                id="t3", status="EXPORTED", config_id=None, exported_at=now - dt.timedelta(days=40)
            )
        )
        s.add(Transaction(id="t4", status="WORKING", config_id="c7", exported_at=None))
        s.add(
            Transaction(
                id="t5", status="EXPORTED", config_id="c7", exported_at=now - dt.timedelta(days=2)
            )
        )
        s.commit()


def test_find_purgeable(core_db):
    from alambic_core.services import find_purgeable_transactions

    now = dt.datetime.now(dt.UTC)
    _seed(core_db, now)
    eligible = sorted(find_purgeable_transactions(now=now))
    # t1 (10j > 7j config), t3 (40j > 30j global). Pas t2 (90j), t4 (non exportée), t5 (2j).
    assert eligible == ["t1", "t3"]


def test_purge_expired(core_db):
    from alambic_core.models import Transaction
    from alambic_core.services import purge_expired_transactions

    now = dt.datetime.now(dt.UTC)
    _seed(core_db, now)
    results = purge_expired_transactions(now=now)
    assert {r.transaction_id for r in results} == {"t1", "t3"}
    with core_db() as s:
        remaining = sorted(t.id for t in s.query(Transaction).all())
        assert remaining == ["t2", "t4", "t5"]


def test_config_retention_days(core_db):
    from alambic_core.models import Config
    from alambic_core.services import config_retention_days, global_retention_days

    # Config avec valeur explicite.
    c = Config(id="x", config_name="x", general={"retention_days": 15})
    assert config_retention_days(c) == 15
    # Config sans valeur → repli global.
    c2 = Config(id="y", config_name="y", general={})
    assert config_retention_days(c2) == global_retention_days() == 30
    # Config None → global.
    assert config_retention_days(None) == 30


def test_invalid_retention_falls_back(core_db):
    from alambic_core.models import Config
    from alambic_core.services import config_retention_days

    c = Config(id="z", config_name="z", general={"retention_days": "pas un nombre"})
    assert config_retention_days(c) == 30
