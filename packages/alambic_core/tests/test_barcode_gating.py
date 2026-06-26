"""Tests du gating CAB (décision de lecture des codes-barres)."""

from __future__ import annotations

import json

from alambic_core.services.barcode_gating import doctype_needs_cab


def _doctype(fields):
    return json.dumps({"document_type": "x", "fields": fields})


def test_cab_needed_when_field_has_bcr_type():
    dt = _doctype(
        [
            {"field_name": "num", "bcr_type": ""},
            {"field_name": "recommande", "bcr_type": "Code128"},
        ]
    )
    assert doctype_needs_cab(dt) is True


def test_cab_not_needed_without_bcr_type():
    dt = _doctype([{"field_name": "a", "bcr_type": ""}, {"field_name": "b"}])
    assert doctype_needs_cab(dt) is False


def test_cab_gating_robust_to_bad_json():
    assert doctype_needs_cab("not json") is False
    assert doctype_needs_cab("") is False
    assert doctype_needs_cab("{}") is False


def test_cab_gating_whitespace_bcr_ignored():
    # Un bcr_type qui n'est que des espaces ne compte pas.
    dt = _doctype([{"field_name": "a", "bcr_type": "   "}])
    assert doctype_needs_cab(dt) is False
