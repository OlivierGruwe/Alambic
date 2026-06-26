"""Tests du client storage partagé (clé d'upload, format)."""

from __future__ import annotations

from alambic_core.storage import (
    DEFAULT_ORIGIN,
    UPLOADS_PREFIX,
    build_upload_key,
    input_bucket,
)


def test_build_upload_key_format():
    key = build_upload_key("acc1", "cfg1", "facture.pdf")
    assert key == f"{UPLOADS_PREFIX}/acc1/cfg1/{DEFAULT_ORIGIN}/facture.pdf"


def test_build_upload_key_custom_origin():
    key = build_upload_key("a", "c", "f.pdf", origin="FTP")
    assert key == "__uploads__/a/c/FTP/f.pdf"


def test_default_origin_is_ui_import():
    # Doit correspondre à une origine reconnue par start_ingestion.
    assert DEFAULT_ORIGIN == "UI_IMPORT"


def test_input_bucket_default(monkeypatch):
    monkeypatch.delenv("ALAMBIC_S3_INPUT_BUCKET", raising=False)
    assert input_bucket() == "alambic-input"
    monkeypatch.setenv("ALAMBIC_S3_INPUT_BUCKET", "mon-bucket")
    assert input_bucket() == "mon-bucket"
