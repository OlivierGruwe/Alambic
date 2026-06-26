"""Test d'intégration de la tâche de classification."""

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


def test_classify_persists_doctype(monkeypatch):
    """La tâche classe le document et persiste le doctype identifié."""
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Config, Document, Transaction

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X"))
        s.add(
            Document(
                id="d1",
                transaction_id="tx1",
                status="C",
                process="X",
                ocr_markdown="[PAGE 1] Facture numero 123 montant 1500 EUR",
            )
        )
        s.add(Config(id="cfg1", config_name="c", edenai_settings={}, account_id="acc1"))
        s.commit()

    from alambic_core.ai.document_classifier import ClassificationResult

    import alambic_workers.tasks.classify as classify_task

    # Mock du classifier : renvoie un doctype sans appeler EdenAI.
    class _FakeClassifier:
        def classify_document(self, text):
            return ClassificationResult(
                type="facture",
                description="Une facture",
                confidence=0.9,
                source="llm_vt",
                cost=0.03,
            )

    monkeypatch.setattr(classify_task, "_get_classifier", lambda config: _FakeClassifier())

    payload = {
        "transaction": {"transactionId": "tx1"},
        "configId": "cfg1",
        "accountId": "acc1",
        "document": {"documentId": "d1"},
    }
    result = classify_task.classify_document(payload)

    assert result["classification"]["type"] == "facture"

    from alambic_core.models import Cost, Document

    with Sess() as s:
        d = s.get(Document, "d1")
        assert d.doctype == "facture"
        assert d.doctype_desc == "Une facture"
        # Coût tracé (process CLASSIFY).
        costs = s.query(Cost).filter(Cost.process == "CLASSIFY").all()
        assert len(costs) == 1
        assert float(costs[0].amount) == 0.03

    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)


def test_classify_skips_without_text(monkeypatch):
    """Pas de markdown OCR → classification sautée."""
    dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name  # noqa: SIM115
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{dbfile}"
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()

    from alambic_core.db.base import Base
    from alambic_core.db.session import get_engine, get_sessionmaker, init_core

    init_core()
    import alambic_core.models  # noqa: F401
    from alambic_core.models import Config, Document, Transaction

    Base.metadata.create_all(get_engine())
    Sess = get_sessionmaker()

    with Sess() as s:
        s.add(Transaction(id="tx1", status="W", process="X"))
        s.add(Document(id="d1", transaction_id="tx1", status="C", process="X", ocr_markdown=""))
        s.add(Config(id="cfg1", config_name="c", edenai_settings={}, account_id="acc1"))
        s.commit()

    import alambic_workers.tasks.classify as classify_task

    payload = {
        "transaction": {"transactionId": "tx1"},
        "configId": "cfg1",
        "accountId": "acc1",
        "document": {"documentId": "d1"},
    }
    result = classify_task.classify_document(payload)
    assert result["classification"]["skipped"] == "no_text"

    get_engine().dispose()
    with suppress(OSError, PermissionError):
        os.unlink(dbfile)
