"""Test de la relance de transaction."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress

from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def test_retry_relaunches_unfinished_docs(monkeypatch):
    """Relance : seuls les documents non terminés sont réinjectés."""
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Document, Transaction

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()

    with Sess() as s:
        s.add(
            Transaction(
                id="tx1", status="WORKING", process="X", config_id="cfg1", account_id="acc1"
            )
        )
        # 1 validé (ne pas relancer), 1 en cours (relancer), 1 discarded (ignorer).
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="VALIDATED",
                process="X",
                bucket_name="b",
                object_key="k1",
            )
        )
        s.add(
            Document(
                id="d2",
                transaction_id="tx1",
                status="OCR_DONE",
                process="X",
                bucket_name="b",
                object_key="k2",
            )
        )
        s.add(
            Document(
                id="d3",
                transaction_id="tx1",
                status="DISCARDED",
                process="X",
                bucket_name="b",
                object_key="k3",
            )
        )
        s.commit()

    # Capturer les réinjections sans lancer Celery.
    import alambic_workers.orchestration.processing as proc
    import alambic_workers.tasks.retry as retry_mod

    captured = []
    monkeypatch.setattr(
        proc.run_processing, "apply_async", lambda args, queue: captured.append(args[0])
    )

    result = retry_mod.retry_transaction("tx1")

    assert result["relaunched"] == 1  # seul d2
    assert captured[0]["document"]["documentId"] == "d2"
    assert captured[0]["configId"] == "cfg1"

    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)
