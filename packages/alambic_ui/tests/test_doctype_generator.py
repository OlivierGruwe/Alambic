"""Tests du générateur de doctype depuis PDF (parsing, erreurs, non-configuré)."""

from __future__ import annotations

import pytest

from alambic_ui.doctype_generator import (
    GenerationError,
    NotConfiguredError,
    _parse_llm_response,
    build_prompt,
    call_edenai,
)


def test_not_configured_when_no_endpoint():
    with pytest.raises(NotConfiguredError):
        call_edenai("", "", "mistral", "x", [])


def test_parse_llm_response_basic():
    resp = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"document_type":"f","fields":['
                        '{"field_name":"numero","field_type":"string",'
                        '"field_description":"d"},'
                        '{"field_name":"montant","field_type":"float"}]}'
                    )
                }
            }
        ]
    }
    fields = _parse_llm_response(resp)
    assert len(fields) == 2
    assert fields[0]["field_name"] == "numero"
    assert fields[1]["field_type"] == "float"
    # Structure complète (16 attributs)
    assert "regexp" in fields[0] and "use_ia" in fields[0]


def test_parse_strips_json_fence():
    resp = {
        "choices": [
            {
                "message": {
                    "content": '```json\n{"fields":[{"field_name":"x","field_type":"string"}]}\n```'
                }
            }
        ]
    }
    fields = _parse_llm_response(resp)
    assert len(fields) == 1 and fields[0]["field_name"] == "x"


def test_parse_ignores_empty_field_name():
    resp = {
        "choices": [
            {
                "message": {
                    "content": '{"fields":[{"field_name":"","field_type":"string"},'
                    '{"field_name":"ok","field_type":"string"}]}'
                }
            }
        ]
    }
    fields = _parse_llm_response(resp)
    assert len(fields) == 1 and fields[0]["field_name"] == "ok"


def test_parse_invalid_type_fallback():
    resp = {
        "choices": [
            {"message": {"content": '{"fields":[{"field_name":"x","field_type":"weird"}]}'}}
        ]
    }
    assert _parse_llm_response(resp)[0]["field_type"] == "string"


def test_parse_invalid_json_raises():
    with pytest.raises(GenerationError):
        _parse_llm_response({"choices": [{"message": {"content": "not json"}}]})


def test_build_prompt_structure():
    msgs = build_prompt("doc text")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "doc text" in msgs[1]["content"]
