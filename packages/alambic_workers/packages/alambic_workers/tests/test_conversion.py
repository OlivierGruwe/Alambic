"""Tests de la conversion au format pivot PDF (brique D)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from alambic_workers.conversion import (
    convert_image,
    convert_text,
    convert_to_pdf,
    detect_kind,
)


def test_detect_kind(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("hello")
    assert detect_kind(str(txt)) == "text"

    png = tmp_path / "a.png"
    Image.new("RGB", (10, 10), "white").save(png)
    assert detect_kind(str(png)) == "image"

    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK\x03\x04fake")  # zip-like, mais extension office
    assert detect_kind(str(docx)) == "office"


def test_convert_text(tmp_path):
    txt = tmp_path / "doc.txt"
    txt.write_text("Bonjour Marilou.\nLigne accentuée éàü çœ.\n" * 5, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    pdf, pages = convert_text(str(txt), str(out))
    assert Path(pdf).exists()
    assert pages >= 1


def test_convert_image(tmp_path):
    img = tmp_path / "scan.png"
    Image.new("RGB", (800, 1000), "white").save(img)
    out = tmp_path / "out"
    out.mkdir()
    pdf, pages = convert_image(str(img), str(out))
    assert Path(pdf).exists()
    assert pages == 1


def test_pdf_passthrough(tmp_path):
    # Un PDF en entrée n'est pas reconverti.
    img = tmp_path / "x.png"
    Image.new("RGB", (100, 100), "white").save(img)
    out = tmp_path / "out"
    out.mkdir()
    pdf, _ = convert_image(str(img), str(out))
    p2, pages2, kind2 = convert_to_pdf(pdf, str(tmp_path / "out2"))
    assert kind2 == "pdf"
    assert p2 == pdf


def test_unknown_type_raises(tmp_path):
    f = tmp_path / "x.xyz"
    f.write_bytes(b"\x00\x01\x02random")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError):
        convert_to_pdf(str(f), str(out))


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice non installé")
def test_convert_office(tmp_path):
    # Génère un .odt via soffice puis le convertit en PDF.
    import subprocess

    src = tmp_path / "letter.txt"
    src.write_text("Document Office de test.", encoding="utf-8")
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "odt", "--outdir", str(tmp_path), str(src)],
        capture_output=True,
        timeout=120,
    )
    odt = tmp_path / "letter.odt"
    if not odt.exists():
        pytest.skip("conversion odt indisponible")
    out = tmp_path / "out"
    out.mkdir()
    pdf, pages, kind = convert_to_pdf(str(odt), str(out))
    assert kind == "office"
    assert Path(pdf).exists()
    assert pages >= 1
