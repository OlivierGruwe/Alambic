"""Test d'intégration de la tâche d'export d'un document validé."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _setup():
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()
    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401

    Base.metadata.create_all(get_engine())
    return dbfile, get_sessionmaker(), get_engine()


def test_export_validated_document():
    dbfile, Sess, engine = _setup()
    from alambic_core.db.types import get_secret_provider
    from alambic_core.models import Config, Document, DocumentIndex, Transaction

    enc_token = get_secret_provider().encrypt("tok-plain")
    with Sess() as s:
        from alambic_core.models import Account

        s.add(Account(id="acc1", account_name="ACME", enrich_allowed_domains="export.example"))
        s.add(
            Transaction(
                id="tx1", status="VALIDATED", process="X", config_id="cfg1", account_id="acc1"
            )
        )
        s.add(
            Config(
                id="cfg1",
                config_name="C",
                account_id="acc1",
                edenai_settings={
                    "way_out": "WS",
                    "ws_url": "https://export.example/api",
                    "ws_auth_type": "bearer",
                    "ws_token_enc": enc_token,
                },
            )
        )
        s.add(
            Document(
                id="doc1",
                transaction_id="tx1",
                status="VALIDATED",
                process="X",
                bucket_name="work",
                object_key="path/doc1.pdf",
                doctype="facture",
            )
        )
        s.add(
            DocumentIndex(
                document_id="doc1",
                index_type="extracted",
                index_name="montant",
                index_value="100",
                index_score="1.0",
            )
        )
        s.commit()

    import alambic_workers.tasks.export as exp_mod

    with patch.object(exp_mod.storage, "get_bytes", return_value=b"%PDF-fake"):
        resp = MagicMock()
        resp.status_code = 200
        with (
            patch("requests.post", return_value=resp),
            patch(
                "alambic_core.security.url_guard._host_resolves_to_blocked_ip",
                return_value=False,
            ),
        ):
            result = exp_mod.export_document("doc1")

    assert result["ok"] is True
    assert result["status"] == "EXPORTED"

    with Sess() as s:
        assert s.get(Document, "doc1").status == "EXPORTED"
        assert s.get(Transaction, "tx1").exported_at is not None

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_export_skips_non_validated():
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Document, Transaction

    with Sess() as s:
        s.add(Transaction(id="tx1", status="WORKING", process="X"))
        s.add(Document(id="doc1", transaction_id="tx1", status="OCR_DONE", process="X"))
        s.commit()

    import alambic_workers.tasks.export as exp_mod

    result = exp_mod.export_document("doc1")
    assert result["ok"] is False
    assert "statut_invalide" in result["error"]

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_export_blocked_when_incomplete():
    """L'export est bloqué si le dossier est incomplet (doctype obligatoire manquant)."""
    dbfile, Sess, engine = _setup()
    from alambic_core.models import Account, Config, Document, Transaction

    with Sess() as s:
        s.add(Account(id="acc1", account_name="A", enrich_allowed_domains="x.com"))
        s.add(
            Config(
                id="cfg1",
                config_name="Assurance",
                account_id="acc1",
                completeness_check=True,
                expected_doctypes=[
                    {"doctype_id": "CNI", "required": True},
                    {"doctype_id": "CARTE_GRISE", "required": True},
                ],
                edenai_settings={"way_out": "WS", "ws_url": "https://x.com/api"},
            )
        )
        s.add(
            Transaction(
                id="tx1", status="VALIDATED", process="X", config_id="cfg1", account_id="acc1"
            )
        )
        # Manque CARTE_GRISE.
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="VALIDATED",
                process="X",
                doctype="CNI",
                bucket_name="work",
                object_key="k/d1.pdf",
            )
        )
        s.commit()

    import alambic_workers.tasks.export as exp_mod

    result = exp_mod.export_document("d1")
    assert result["ok"] is False
    assert result["error"] == "dossier_incomplet"
    assert result["missing_required"] == ["CARTE_GRISE"]

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_export_override_bypasses_completeness():
    """Le déblocage manuel (completeness_override) permet l'export d'un dossier incomplet."""
    dbfile, Sess, engine = _setup()
    from unittest.mock import patch

    from alambic_core.models import Account, Config, Document, Transaction

    with Sess() as s:
        s.add(Account(id="acc1", account_name="A", enrich_allowed_domains="x.com"))
        s.add(
            Config(
                id="cfg1",
                config_name="Assurance",
                account_id="acc1",
                completeness_check=True,
                expected_doctypes=[
                    {"doctype_id": "CNI", "required": True},
                    {"doctype_id": "CARTE_GRISE", "required": True},
                ],
                edenai_settings={"way_out": "WS", "ws_url": "https://x.com/api"},
            )
        )
        s.add(
            Transaction(
                id="tx1",
                status="VALIDATED",
                process="X",
                config_id="cfg1",
                account_id="acc1",
                completeness_override=True,
            )
        )
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="VALIDATED",
                process="X",
                doctype="CNI",
                bucket_name="work",
                object_key="k/d1.pdf",
            )
        )
        s.commit()

    import alambic_workers.tasks.export as exp_mod

    with patch.object(exp_mod.storage, "get_bytes", return_value=b"%PDF"):
        resp = MagicMock()
        resp.status_code = 200
        with (
            patch("requests.post", return_value=resp),
            patch(
                "alambic_core.security.url_guard._host_resolves_to_blocked_ip",
                return_value=False,
            ),
        ):
            result = exp_mod.export_document("d1")

    assert result["ok"] is True

    engine.dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)
