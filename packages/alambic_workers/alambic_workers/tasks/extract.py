"""alambic_workers.tasks.extract — extraction des champs d'un document.

Porté de FlowerScan (ai_extraction.handler), adapté à l'architecture Alambic
(Celery + repos SQLAlchemy + Garage), sans Lambda ni ASL.

Deux passes selon la stratégie de chaque champ du doctype :
  - conventionnelle (use_ia=0 avec regexp/anchors/zone/barcode) → field_extractor,
    qui lit l'OCR positionné (ocr_lines) et les codes-barres (barcodes) ;
  - LLM (use_ia=1) → llm_extractor (EdenAI), qui lit le markdown OCR.

Les index des deux passes sont fusionnés (LLM gagne en cas de conflit), persistés
dans document_indexes, et un résumé de qualité (extraction_summary) est calculé
puis stocké sur le document. Le coût LLM est tracé dans la table Cost.
"""

from __future__ import annotations

import json
import logging

from alambic_core.db.session import session_scope
from alambic_core.models import Config, Doctype, Document, DocumentIndex
from alambic_core.services.extraction import (
    compute_extraction_summary,
    split_fields_by_strategy,
)

logger = logging.getLogger(__name__)

PROCESS_EXTRACT = "EXTRACT"

# Cache des extracteurs LLM par config (comme le classifier).
_EXTRACTOR_CACHE: dict = {}


def _get_extractor(config):
    """Extracteur LLM pour cette config (mis en cache)."""
    from alambic_core.ai.llm_extractor import LLMExtractor, extractor_config_from_config

    key = config.id
    if key not in _EXTRACTOR_CACHE:
        _EXTRACTOR_CACHE[key] = LLMExtractor(extractor_config_from_config(config))
    return _EXTRACTOR_CACHE[key]


def _doctype_fields(config, doc_id: str) -> tuple[list, str, str]:
    """Champs + nom + description du doctype RÉELLEMENT classifié pour ce document.

    Source de vérité : document.doctype (le type déterminé par la classification
    pour CE document), résolu en Doctype dans le périmètre du compte de la config
    (doctypes publics + ceux du compte). Repli sur config.doctype_id pour les
    anciennes configs mono-doctype. Lit doctype.json_content (JSON {fields,
    description}). Renvoie ([], "", "") si rien d'exploitable.
    """
    from sqlalchemy import or_

    with session_scope() as s:
        doc = s.get(Document, doc_id)
        classified = (doc.doctype if doc is not None else "") or ""

        dt = None
        # 1. Résolution par le type classifié du document (par nom), dans le
        #    périmètre du compte (publics + compte de la config).
        if classified and classified != "unknown":
            q = s.query(Doctype).filter(Doctype.doctype_name == classified)
            if config.account_id:
                q = q.filter(
                    or_(Doctype.is_public.is_(True), Doctype.account_id == config.account_id)
                )
            dt = q.first()

        # 2. Repli : ancien doctype_id de la config (compat mono-doctype).
        if dt is None and getattr(config, "doctype_id", ""):
            dt = s.get(Doctype, config.doctype_id)

        if dt is None:
            return [], "", ""

        name = dt.doctype_name or ""
        try:
            content = json.loads(dt.json_content or "{}")
        except (json.JSONDecodeError, TypeError):
            content = {}
        fields = content.get("fields") or []
        description = content.get("description") or ""
    return fields, name, description


def _ocr_pages_for_conventional(doc) -> dict:
    """Construit le dict page-keyed attendu par field_extractor depuis ocr_lines.

    ocr_lines stocke to_json()["pages"] : liste de pages {page, lines, barcodes}.
    field_extractor.extract_field_from_pages attend {"1": {lines, barcodes}, ...}.
    """
    pages = doc.ocr_lines or []
    keyed = {}
    for p in pages:
        try:
            num = int(p.get("page"))
        except (TypeError, ValueError):
            continue
        keyed[str(num)] = {
            "lines": p.get("lines", []) or [],
            "barcodes": p.get("barcodes", []) or doc.barcodes or [],
        }
    return keyed


def _run_conventional_pass(fields: list, doc, doc_id: str) -> list:
    """Extraction conventionnelle (regex/anchor/zone/barcode). Score binaire 1/0."""
    from alambic_core.ai.field_extractor import extract_field_from_pages

    pages = _ocr_pages_for_conventional(doc)
    indexes = []
    mrz_cache: dict = {}
    for field in fields:
        try:
            value = extract_field_from_pages(pages, field, mrz_cache=mrz_cache) or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Extraction conventionnelle échouée (%s) : %s", field.get("field_name"), exc
            )
            value = ""
        indexes.append(
            {
                "index_name": field.get("field_name"),
                "index_value": value,
                "index_score": "1.0" if value else "0.0",
                "index_desc": field.get("field_description", ""),
                "document_id": doc_id,
            }
        )
    return indexes


def _persist_extraction(doc_id, tx_id, account_id, indexes, summary, config) -> None:
    """Persiste le résumé sur le document et les index non-vides.

    Décide aussi du statut de validation : VALIDATED (auto) ou PENDING_VALIDATION
    (humain), selon need_validation et le seuil de confiance des champs.
    """
    from alambic_core.services.auto_validation import decide_validation_status

    status = decide_validation_status(config, indexes)
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is not None:
            doc.extraction_summary = summary
            # Validation humaine (PENDING_VALIDATION) ou automatique (VALIDATED)
            # selon need_validation et l'indice de confiance des champs extraits.
            doc.status = status
            # Remplace les index extraits existants (réexécution idempotente).
            s.query(DocumentIndex).filter(
                DocumentIndex.document_id == doc_id,
                DocumentIndex.index_type == "extracted",
            ).delete()
            for idx in indexes:
                if not idx.get("index_name") or _empty(idx.get("index_value")):
                    continue
                s.add(
                    DocumentIndex(
                        document_id=doc_id,
                        index_type="extracted",
                        index_name=idx["index_name"],
                        index_value=str(idx.get("index_value") or ""),
                        index_score=str(idx.get("index_score") or ""),
                        index_desc=idx.get("index_desc", "") or "",
                    )
                )


def _empty(value) -> bool:
    return value is None or str(value).strip() == ""


def extract_document(payload: dict) -> dict:
    """Extrait les champs du document et persiste index + résumé. Renvoie le payload."""
    from alambic_core.pipeline.step import step

    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document") or {}
    doc_id = doc.get("documentId")
    config_id = payload.get("configId")
    account_id = payload.get("accountId")

    with step(tx_id, PROCESS_EXTRACT, document_id=doc_id) as st:
        if st.skipped:
            return payload

        with session_scope() as s:
            config = s.get(Config, config_id) if config_id else None
            if config is None:
                logger.warning("Extraction : config %s introuvable, étape sautée", config_id)
                payload["extraction"] = {"skipped": "no_config"}
                return payload

        fields, doctype_name, doctype_desc = _doctype_fields(config, doc_id)
        if not fields:
            logger.info("Extraction sautée (doctype sans champs) pour %s", doc_id)
            payload["extraction"] = {"skipped": "no_fields"}
            return payload

        llm_fields, conventional_fields, skipped = split_fields_by_strategy(fields)
        logger.info(
            "Extraction %s : %d LLM, %d conventionnels, %d ignorés",
            doc_id,
            len(llm_fields),
            len(conventional_fields),
            len(skipped),
        )

        # ── Passe conventionnelle (regex/anchor/zone/barcode) ──────────────
        conventional_indexes = []
        cost_payload = {}
        with session_scope() as s:
            d = s.get(Document, doc_id)
            if conventional_fields and d is not None:
                conventional_indexes = _run_conventional_pass(conventional_fields, d, doc_id)
            ocr_markdown = (d.ocr_markdown if d is not None else "") or ""

        # ── Passe LLM (use_ia=1) ───────────────────────────────────────────
        llm_indexes = []
        if llm_fields:
            extractor = _get_extractor(config)
            res = extractor.extract(
                text=ocr_markdown,
                doctype_name=doctype_name,
                doctype_desc=doctype_desc,
                fields=llm_fields,
            )
            cost_payload = res.get("extraction", {})
            desc_map = {f.get("field_name"): f.get("field_description", "") for f in llm_fields}
            llm_indexes = [
                {
                    "index_name": name,
                    "index_value": v.get("value"),
                    "index_score": v.get("score"),
                    "index_desc": desc_map.get(name, ""),
                    "document_id": doc_id,
                }
                for name, v in res.get("indexes", {}).items()
            ]

        # ── Fusion (LLM gagne en cas de conflit) ───────────────────────────
        merged = {}
        for idx in conventional_indexes + llm_indexes:
            merged[idx["index_name"]] = idx
        all_indexes = list(merged.values())

        summary = compute_extraction_summary(all_indexes, fields)
        _persist_extraction(doc_id, tx_id, account_id, all_indexes, summary, config)

        # Trace du coût : TOUJOURS écrite (même à 0, ex. extraction 100% conventionnelle).
        from alambic_core.services.cost_tracking import record_cost

        record_cost(
            process=PROCESS_EXTRACT,
            amount=float((cost_payload or {}).get("cost", 0) or 0),
            transaction_id=tx_id,
            document_id=doc_id,
            account_id=account_id,
            provider=(cost_payload or {}).get("provider", "") or "",
            model=(cost_payload or {}).get("model", "") or "",
            details=f"{len(llm_fields)}_llm_{len(conventional_fields)}_conv",
        )

        logger.info(
            "Extraction terminée %s : %d/%d champs, ok=%s",
            doc_id,
            summary["extracted_fields"],
            summary["total_fields"],
            summary["extraction_ok"],
        )

        payload["extraction"] = {
            "indexes": len(all_indexes),
            "summary": summary,
            "cost": float((cost_payload or {}).get("cost", 0) or 0),
        }
        return payload
