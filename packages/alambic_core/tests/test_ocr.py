"""Tests du client OCR EdenAI et du moteur d'extraction hybride."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import fitz

from alambic_core.ai.circuit_breaker import CircuitBreaker
from alambic_core.ai.edenai_ocr import DocumentOcr, OcrConfig, OcrResult, _is_poor_ocr
from alambic_core.ai.ocr_parsing import parse_positioned_lines
from alambic_core.ai.pdf_extractor import PdfExtractor, b64d, b64e, detect_page_type


# ── Parsing positions ────────────────────────────────────────────────────────
def test_textract_parsing():
    resp = {
        "amazon": {
            "Blocks": [
                {
                    "BlockType": "LINE",
                    "Text": "Permis AM",
                    "Page": 1,
                    "Geometry": {
                        "BoundingBox": {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.05}
                    },
                },
                {"BlockType": "WORD", "Text": "ignoré"},
            ]
        }
    }
    lines = parse_positioned_lines(resp, "ocr/ocr/amazon")
    assert len(lines) == 1
    assert lines[0]["text"] == "Permis AM"
    assert lines[0]["bbox"]["x0"] == 10.0


def test_unknown_provider_returns_empty():
    assert parse_positioned_lines({"x": 1}, "inconnu") == []


# ── Circuit breaker ──────────────────────────────────────────────────────────
def test_circuit_breaker_opens_and_resets():
    cb = CircuitBreaker(failure_threshold=2)
    assert cb.allow()
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert not cb.allow()  # ouvert
    cb.record_success()
    assert cb.allow()  # refermé


# ── Client OCR ───────────────────────────────────────────────────────────────
def _resp(json_data):
    r = MagicMock()
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


def _cfg():
    return OcrConfig(
        secret_key="k",
        endpoint="https://ed/ocr",
        provider="ocr/mistral",
        fallback_provider="ocr/amazon",
    )


def test_ocr_primary_success():
    with patch("alambic_core.ai.edenai_ocr._build_session") as mk:
        sess = MagicMock()
        sess.post.side_effect = [
            _resp({"file_id": "f"}),
            _resp({"provider": "mistral", "cost": 0.02, "output": {"text": "Permis AM Marilou"}}),
        ]
        mk.return_value = sess
        ocr = DocumentOcr(_cfg())
        res = ocr.ocr_bytes(b"img", "x.png")
    assert res.text.startswith("Permis")
    assert res.cost == 0.02


def test_ocr_fallback_on_poor_result():
    with patch("alambic_core.ai.edenai_ocr._build_session") as mk:
        sess = MagicMock()
        sess.post.side_effect = [
            _resp({"file_id": "f"}),
            _resp({"provider": "mistral", "cost": 0.01, "output": {"text": ""}}),  # pauvre
            _resp(
                {
                    "provider": "amazon",
                    "cost": 0.03,
                    "output": {"text": "Texte complet du fallback"},
                }
            ),
        ]
        mk.return_value = sess
        ocr = DocumentOcr(_cfg())
        res = ocr.ocr_bytes(b"img", "x.png")
    assert res.provider == "amazon"
    assert abs(res.cost - 0.04) < 1e-9  # cumul primary + fallback


def test_is_poor_ocr():
    assert _is_poor_ocr({"text": "", "bounding_boxes": []}) is True
    assert _is_poor_ocr({"text": "court"}) is True
    assert _is_poor_ocr({"text": "Un texte suffisamment long pour passer"}) is False


# ── b64 ──────────────────────────────────────────────────────────────────────
def test_b64_roundtrip():
    assert b64d(b64e("Café à 10€")) == "Café à 10€"
    assert b64d("invalide!!!") == ""


# ── Moteur hybride ───────────────────────────────────────────────────────────
def _native_pdf():
    pdf = tempfile.mktemp(suffix=".pdf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Mise en demeure bancaire, contrat 12345.", fontsize=11)
    doc.save(pdf)
    doc.close()
    return pdf


def test_pdf_extractor_produces_markdown_and_json():
    ocr_mock = MagicMock()
    ocr_mock.ocr_bytes.return_value = OcrResult(text="zone ocr", provider="amazon", cost=0.01)
    ext = PdfExtractor(_native_pdf(), ocr_mock)
    ext.parse()
    j = ext.to_json()
    assert "pages" in j and len(j["pages"]) == 1
    md = ext.to_markdown()
    assert "[PAGE 1]" in md
    assert "Mise en demeure" in md


def test_pdf_extractor_injects_barcodes():
    ocr_mock = MagicMock()
    ocr_mock.ocr_bytes.return_value = OcrResult(text="x")
    barcodes = [{"value": "SEP12345", "page": 1, "position": {"x0": 1, "y0": 1, "x1": 2, "y1": 2}}]
    ext = PdfExtractor(_native_pdf(), ocr_mock, barcodes=barcodes)
    ext.parse()
    md = ext.to_markdown()
    assert "SEP12345" in md
    bc = [ln for p in ext.to_json()["pages"] for ln in p["lines"] if ln.get("source") == "barcode"]
    assert len(bc) == 1


def test_detect_page_type_scan():

    from PIL import Image

    pdf = tempfile.mktemp(suffix=".pdf")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    ip = tempfile.mktemp(suffix=".png")
    Image.new("RGB", (400, 300), "white").save(ip)
    page.insert_image(fitz.Rect(100, 100, 500, 400), filename=ip)
    doc.save(pdf)
    doc.close()
    assert detect_page_type(fitz.open(pdf).load_page(0)) == "SCAN"


def test_extract_secret_key_from_json():
    from alambic_core.ai.edenai_ocr import _extract_secret_key

    # edenai_secret_enc déchiffré = JSON {"secret_key": ...}
    assert _extract_secret_key('{"secret_key": "edenai_real_key"}') == "edenai_real_key"
    # rétrocompat clé brute
    assert _extract_secret_key("brute") == "brute"
    # vide / sans clé
    assert _extract_secret_key("") == ""
    assert _extract_secret_key('{"x": 1}') == ""


# ── Parsing réel des deux providers (captures de production) ──────────────────
def test_mistral_pages_markdown_real_structure():
    """Structure Mistral réelle : original_response.pages[].markdown, blocks=null."""
    from alambic_core.ai.ocr_parsing import mistral_pages_markdown

    original = {
        "pages": [
            {
                "index": 0,
                "markdown": "# TRAIL\n![img-0.jpeg](img-0.jpeg)\nDIMANCHE 21 MARS 2027",
                "images": [{"id": "img-0.jpeg", "top_left_x": 949}],
                "dimensions": {"dpi": 200, "height": 3508, "width": 2481},
                "blocks": None,
            }
        ],
        "model": "mistral-ocr-latest",
    }
    pages = mistral_pages_markdown(original)
    assert len(pages) == 1
    assert pages[0]["page"] == 1  # index 0 → page 1
    assert "# TRAIL" in pages[0]["markdown"]
    assert "![img" not in pages[0]["markdown"]  # images retirées
    # Mistral ne donne pas de lignes positionnées
    from alambic_core.ai.ocr_parsing import parse_positioned_lines

    assert parse_positioned_lines(original, "mistral") == []


def test_amazon_textract_real_structure():
    """Structure Amazon réelle : original_response.Blocks[] LINE avec BoundingBox."""
    from alambic_core.ai.ocr_parsing import parse_positioned_lines

    original = {
        "DocumentMetadata": {"Pages": 1},
        "Blocks": [
            {"BlockType": "PAGE", "Id": "p1"},
            {
                "BlockType": "LINE",
                "Text": "VIGILANCE RENFORCÉE",
                "Geometry": {
                    "BoundingBox": {
                        "Width": 0.5468,
                        "Height": 0.0393,
                        "Left": 0.1941,
                        "Top": 0.0539,
                    }
                },
                "Id": "l1",
            },
            {"BlockType": "WORD", "Text": "VIGILANCE"},  # ignoré
        ],
    }
    lines = parse_positioned_lines(original, "amazon")
    assert len(lines) == 1  # seulement la LINE
    assert lines[0]["text"] == "VIGILANCE RENFORCÉE"
    assert abs(lines[0]["bbox"]["x0"] - 19.41) < 0.1
    assert abs(lines[0]["bbox"]["x1"] - 74.09) < 0.5  # (0.1941+0.5468)*100
    assert lines[0]["page"] == 1  # pas de champ Page → défaut


# ── Non-régression : détection de pages natives (documents bureautiques aérés) ──


def test_detect_page_type_native_aere():
    """Une page de courrier (texte abondant mais aéré) doit être NATIVE.

    Non-régression : l'ancien seuil text_ratio>0.6 classait à tort ces pages en
    HYBRID, déclenchant un OCR EdenAI inutile sur du PDF déjà lisible. Le nombre
    de caractères doit primer sur le ratio de surface.
    """
    import fitz

    from alambic_core.ai.pdf_extractor import detect_page_type

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Texte abondant mais qui ne couvre qu'une fraction de la surface.
    page.insert_text((72, 100), "Mise en demeure découvert autorisé. " * 30, fontsize=11)
    assert detect_page_type(page) == "NATIVE"
    doc.close()


def test_detect_page_type_scan_still_works():
    """Une page image plein cadre sans texte reste détectée SCAN."""
    import fitz

    from alambic_core.ai.pdf_extractor import detect_page_type

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 595, 842))
    pix.clear_with(200)
    page.insert_image(page.rect, pixmap=pix)
    assert detect_page_type(page) == "SCAN"
    doc.close()
