"""Tests du field_extractor (5 stratégies d'extraction)."""

from __future__ import annotations

import base64

from alambic_core.ai.field_extractor import extract_field


def _b64(t):
    return base64.b64encode(t.encode()).decode()


def _page():
    return {
        "lines": [
            {
                "text": _b64("Numero de contrat: 12345"),
                "position": {"x0": 10, "y0": 10, "x1": 50, "y1": 13},
            },
            {
                "text": _b64("Nom client: Dupont"),
                "position": {"x0": 10, "y0": 20, "x1": 50, "y1": 23},
            },
            {
                "text": _b64("Montant: 1500 EUR"),
                "position": {"x0": 10, "y0": 30, "x1": 50, "y1": 33},
            },
        ],
        "barcodes": [
            {
                "value": "RECO123456789",
                "format": "Code128",
                "position": {"x0": 70, "y0": 5, "x1": 95, "y1": 10},
            },
        ],
    }


def test_extract_barcode():
    assert extract_field(_page(), {"field_name": "x", "bcr_type": "Code128"}) == "RECO123456789"


def test_extract_barcode_tolerant_format():
    # bcr_type "CODE_128" doit matcher "Code128" (normalisation espaces/underscore).
    assert extract_field(_page(), {"field_name": "x", "bcr_type": "CODE_128"}) == "RECO123456789"


def test_extract_regex():
    assert extract_field(_page(), {"field_name": "x", "regexp": r"\d{5}"}) == "12345"


def test_extract_zone():
    f = {"field_name": "x", "regexp": r"\d+ EUR", "page_zone": "0,25,60,40"}
    assert extract_field(_page(), f) == "1500 EUR"


def test_extract_anchor():
    f = {"field_name": "x", "anchors": "Nom client", "regexp": r"[A-Z][a-z]+", "direction": "right"}
    assert extract_field(_page(), f) == "Dupont"


def test_extract_default():
    assert extract_field(_page(), {"field_name": "x", "default_value": "N/A"}) == "N/A"


def test_extract_blacklist():
    # Si la valeur contient un black_word, elle est rejetée → default.
    f = {"field_name": "x", "regexp": r"Dupont", "black_words": "Dupont", "default_value": ""}
    assert extract_field(_page(), f) == ""
