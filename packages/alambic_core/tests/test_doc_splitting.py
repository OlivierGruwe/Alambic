"""Tests du découpage logique (3 stratégies)."""

from __future__ import annotations

import base64

from alambic_core.services.doc_splitting import detect_multidocument, split_pages


def _b64(t):
    return base64.b64encode(t.encode()).decode()


def _bc(value):
    return [
        {"value": value, "format": "Code128", "position": {"x0": 70, "y0": 5, "x1": 95, "y1": 10}}
    ]


def test_fixed_page():
    pages = {i: {"lines": [], "barcodes": []} for i in range(1, 7)}
    assert split_pages(pages, fixed_page=2) == [[1, 2], [3, 4], [5, 6]]


def test_multidoc_barcode_change():
    pages = {
        1: {"lines": [], "barcodes": _bc("A")},
        2: {"lines": [], "barcodes": _bc("A")},
        3: {"lines": [], "barcodes": _bc("B")},
    }
    assert 3 in detect_multidocument(pages)
    assert split_pages(pages) == [[1, 2], [3]]


def test_multidoc_pagination_reset():
    def pg(txt):
        return {
            "lines": [{"text": _b64(txt), "position": {"x0": 0, "y0": 0, "x1": 10, "y1": 3}}],
            "barcodes": [],
        }

    pages = {1: pg("page 1"), 2: pg("page 2"), 3: pg("page 1")}
    assert 3 in detect_multidocument(pages)


def test_separator_field():
    def pg(val):
        return {
            "lines": [
                {
                    "text": _b64(f"Dossier: {val}"),
                    "position": {"x0": 10, "y0": 10, "x1": 50, "y1": 13},
                }
            ],
            "barcodes": [],
        }

    pages = {1: pg("A100"), 2: pg("A100"), 3: pg("B200")}
    sep = {
        "field_name": "d",
        "is_separator": "1",
        "anchors": "Dossier",
        "regexp": r"[AB]\d{3}",
        "direction": "right",
    }
    assert split_pages(pages, fields=[sep]) == [[1, 2], [3]]


def test_no_split_homogeneous():
    pages = {i: {"lines": [], "barcodes": _bc("SAME")} for i in range(1, 4)}
    assert split_pages(pages) == [[1, 2, 3]]
