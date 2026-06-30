"""alambic_core.services.extraction — partition des champs et résumé d'extraction.

Porté de FlowerScan (ai_extraction._split_fields_by_strategy + _compute_extraction_summary).

Deux responsabilités pures (sans I/O) :
- partitionner les champs d'un doctype selon la stratégie d'extraction (LLM pour
  use_ia=1, conventionnelle pour ceux qui ont regex/anchor/zone/barcode, ignorés
  sinon) ;
- calculer un résumé de qualité (champs requis manquants, scores, extraction_ok)
  qui servira plus tard à décider d'une validation humaine.
"""

from __future__ import annotations

DEFAULT_EXTRACTION_SCORE_THRESHOLD = 0.9

_TRUTHY = {"1", "true", "on", "yes"}


def _is_truthy(value) -> bool:
    return str(value if value is not None else "0").strip().lower() in _TRUTHY


def _is_empty(value) -> bool:
    return value is None or str(value).strip() == ""


def split_fields_by_strategy(fields: list) -> tuple[list, list, list]:
    """Partitionne les champs : (llm_fields, conventional_fields, skipped).

    - llm_fields : use_ia=1 → extraction LLM.
    - conventional_fields : use_ia=0 avec au moins une stratégie (regexp/anchors/
      bcr_type/zone) → field_extractor.
    - skipped : use_ia=0 sans stratégie → restent vides (et signalés si required).
    """
    llm_fields, conventional, skipped = [], [], []
    for f in fields or []:
        if _is_truthy(f.get("use_ia", 0)):
            llm_fields.append(f)
            continue
        has_strategy = any(
            not _is_empty(f.get(k)) for k in ("regexp", "anchors", "bcr_type", "zone", "page_zone")
        )
        (conventional if has_strategy else skipped).append(f)
    return llm_fields, conventional, skipped


def compute_extraction_summary(
    indexes: list, fields: list, threshold: float = DEFAULT_EXTRACTION_SCORE_THRESHOLD
) -> dict:
    """Résumé de qualité d'extraction.

    extraction_ok est vrai si (1) aucun champ requis n'est manquant/vide,
    (2) au moins un champ non-vide a été extrait, (3) tous les champs non-vides
    ont un score >= threshold.
    """
    required = {f.get("field_name", "") for f in (fields or []) if _is_truthy(f.get("required", 0))}
    required.discard("")

    by_name = {idx.get("index_name", ""): idx for idx in (indexes or []) if idx.get("index_name")}

    missing_required = sorted(
        fname
        for fname in required
        if fname not in by_name or _is_empty(by_name[fname].get("index_value"))
    )

    populated = [idx for idx in (indexes or []) if not _is_empty(idx.get("index_value"))]
    scores = []
    for idx in populated:
        try:
            scores.append(float(idx.get("index_score") or 0))
        except (TypeError, ValueError):
            scores.append(0.0)

    min_score = min(scores) if scores else 0.0
    avg_score = sum(scores) / len(scores) if scores else 0.0
    extraction_ok = (not missing_required) and bool(scores) and min_score >= threshold

    return {
        "total_fields": len(fields or []),
        "required_fields": len(required),
        "extracted_fields": len(populated),
        "missing_required": missing_required,
        "min_score": round(min_score, 3),
        "avg_score": round(avg_score, 3),
        "threshold": threshold,
        "extraction_ok": extraction_ok,
    }
