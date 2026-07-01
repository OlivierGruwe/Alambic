"""Test de la sélection du moteur OCR (EdenAI vs Tesseract) selon la config."""

from __future__ import annotations

import os
import tempfile

from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _init():
    os.environ["ALAMBIC_DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"  # noqa: SIM115, E501
    os.environ["ALAMBIC_SECRET_KEY"] = Fernet.generate_key().decode()
    from alambic_core.db.session import init_core

    init_core()


def test_engine_tesseract_selected():
    _init()
    from alambic_core.ai.tesseract_ocr import TesseractOcr
    from alambic_core.models import Config

    from alambic_workers.tasks.ocr import _build_ocr_client

    cfg = Config(id="c1", config_name="c", edenai_settings={"ocr_engine": "tesseract"})
    assert isinstance(_build_ocr_client(cfg), TesseractOcr)


def test_engine_edenai_is_default():
    _init()
    from alambic_core.ai.edenai_ocr import DocumentOcr
    from alambic_core.models import Config

    from alambic_workers.tasks.ocr import _build_ocr_client

    # Pas de ocr_engine → EdenAI par défaut.
    cfg = Config(id="c2", config_name="c", edenai_settings={"region": "eu"})
    assert isinstance(_build_ocr_client(cfg), DocumentOcr)
    # Explicitement edenai.
    cfg2 = Config(id="c3", config_name="c", edenai_settings={"ocr_engine": "edenai"})
    assert isinstance(_build_ocr_client(cfg2), DocumentOcr)


def test_engine_cascade_selected():
    _init()
    from alambic_core.ai.cascade_ocr import CascadeOcr
    from alambic_core.models import Config

    from alambic_workers.tasks.ocr import _build_ocr_client

    cfg = Config(id="cc", config_name="c", edenai_settings={"ocr_engine": "cascade"})
    assert isinstance(_build_ocr_client(cfg), CascadeOcr)
