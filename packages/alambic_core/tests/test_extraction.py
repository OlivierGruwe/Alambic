"""Tests du service d'extraction (partition + résumé) et de l'extracteur LLM."""

from __future__ import annotations

from alambic_core.ai.llm_extractor import (
    ExtractorConfig,
    LLMExtractor,
    empty_indexes,
    normalize,
    safe_json,
)
from alambic_core.services.extraction import (
    compute_extraction_summary,
    split_fields_by_strategy,
)

# ── Partition ────────────────────────────────────────────────────────────


def test_split_fields_by_strategy():
    fields = [
        {"field_name": "a", "use_ia": 1},
        {"field_name": "b", "use_ia": 0, "bcr_type": "datamatrix"},
        {"field_name": "c", "use_ia": 0, "regexp": r"\d+"},
        {"field_name": "d", "use_ia": 0},  # rien → skipped
    ]
    llm, conv, skipped = split_fields_by_strategy(fields)
    assert [f["field_name"] for f in llm] == ["a"]
    assert {f["field_name"] for f in conv} == {"b", "c"}
    assert [f["field_name"] for f in skipped] == ["d"]


def test_use_ia_accepts_truthy_variants():
    for val in (1, "1", "true", "yes", "on"):
        llm, _, _ = split_fields_by_strategy([{"field_name": "x", "use_ia": val}])
        assert len(llm) == 1


# ── Résumé ───────────────────────────────────────────────────────────────


def test_summary_all_extracted_ok():
    indexes = [
        {"index_name": "a", "index_value": "1", "index_score": "0.95"},
        {"index_name": "b", "index_value": "2", "index_score": "0.91"},
    ]
    fields = [{"field_name": "a", "required": 1}, {"field_name": "b", "required": 1}]
    s = compute_extraction_summary(indexes, fields, threshold=0.9)
    assert s["extraction_ok"] is True
    assert s["extracted_fields"] == 2
    assert s["missing_required"] == []


def test_summary_missing_required():
    indexes = [{"index_name": "a", "index_value": "1", "index_score": "0.95"}]
    fields = [{"field_name": "a", "required": 1}, {"field_name": "b", "required": 1}]
    s = compute_extraction_summary(indexes, fields, threshold=0.9)
    assert s["extraction_ok"] is False
    assert "b" in s["missing_required"]


def test_summary_score_below_threshold():
    indexes = [{"index_name": "a", "index_value": "1", "index_score": "0.5"}]
    fields = [{"field_name": "a", "required": 1}]
    s = compute_extraction_summary(indexes, fields, threshold=0.9)
    assert s["extraction_ok"] is False


# ── Extracteur LLM : parsing ────────────────────────────────────────────


def test_safe_json_strips_markdown():
    assert safe_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert safe_json('texte {"b": 2} texte') == {"b": 2}
    assert safe_json("pas de json") == {}


def test_normalize_tolerates_bare_value():
    raw = {"nom": {"value": "X", "score": "0.9"}, "ville": "Paris"}
    norm = normalize(raw, ["nom", "ville", "absent"])
    assert norm["nom"]["value"] == "X"
    assert norm["ville"]["value"] == "Paris"
    assert norm["absent"] == {"value": "", "score": "0.0"}


def test_extract_empty_text_no_network():
    ext = LLMExtractor(ExtractorConfig(secret_key="k", endpoint="x", provider="m", model="n"))
    r = ext.extract(text="", doctype_name="f", doctype_desc="", fields=[{"field_name": "a"}])
    assert r["indexes"] == empty_indexes(["a"])


def test_extract_builds_strict_prompt():
    ext = LLMExtractor(ExtractorConfig(secret_key="k", endpoint="x", provider="m", model="n"))
    msgs = ext._build_messages("doc text", [{"field_name": "montant", "field_description": "TTC"}])
    assert "strict information extraction engine" in msgs[0]["content"]
    assert "montant: TTC" in msgs[1]["content"]
    assert "doc text" in msgs[1]["content"]
