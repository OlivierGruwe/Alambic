"""alambic_workers.tasks.classify — étape de classification (brique G, partie classif).

Assemble la cascade de classification (lexical → embedding → LLM) depuis la config,
classe le document à partir de son markdown OCR, persiste le doctype identifié et
ses champs sur le document, trace le coût EdenAI par transaction (process="CLASSIFY"),
puis enchaîne sur l'extraction.

Les composants de la cascade sont mis en cache par config (comme FlowerScan) pour
éviter de recharger le vector store / lexical à chaque document.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from alambic_core.ai.category_registry import CategoryRegistry
from alambic_core.ai.document_classifier import DocumentClassifier
from alambic_core.ai.embedder import EdenAIEmbedder, embedding_config_from_config
from alambic_core.ai.lexical_engine import LexicalEngine
from alambic_core.ai.llm_classifier import LLMClassifier, classifier_config_from_config
from alambic_core.ai.vector_store import CategoryVectorStore
from alambic_core.db.session import session_scope
from alambic_core.models import Config, Cost, Doctype, Document

logger = logging.getLogger(__name__)

PROCESS_CLASSIFY = "CLASSIFY"

# Cache des classifieurs par signature de config (doctypes inclus).
_CLASSIFIER_CACHE: dict = {}
# Vector store et lexical engine partagés (rechargés périodiquement en interne).
_VECTOR_STORE: CategoryVectorStore | None = None
_LEXICAL_ENGINE: LexicalEngine | None = None


def _shared_models() -> tuple[CategoryVectorStore, LexicalEngine]:
    global _VECTOR_STORE, _LEXICAL_ENGINE
    if _VECTOR_STORE is None:
        _VECTOR_STORE = CategoryVectorStore()
    if _LEXICAL_ENGINE is None:
        _LEXICAL_ENGINE = LexicalEngine()
    return _VECTOR_STORE, _LEXICAL_ENGINE


def _load_doctypes(config) -> dict:
    """Charge les doctypes de la config en {doctype_name: {description, fields}}.

    Restreint aux doctypes configurés si edenai_settings.doctype_ids est défini,
    sinon prend tous les doctypes du compte.
    """
    result: dict = {}
    settings = config.edenai_settings or {}
    doctype_ids = settings.get("doctype_ids") or ([config.doctype_id] if config.doctype_id else [])

    with session_scope() as s:
        if doctype_ids:
            doctypes = [s.get(Doctype, did) for did in doctype_ids]
        else:
            doctypes = s.query(Doctype).filter(Doctype.account_id == config.account_id).all()

        for dt in doctypes:
            if dt is None or not dt.json_content:
                continue
            try:
                data = json.loads(dt.json_content)
            except (json.JSONDecodeError, TypeError):
                continue
            result[dt.doctype_name] = {
                "description": data.get("description", ""),
                "fields": data.get("fields", []) or [],
            }
    return result


def _get_classifier(config) -> DocumentClassifier:
    """Assemble (ou récupère du cache) le classifieur pour cette config."""
    settings = config.edenai_settings or {}
    ids_sig = ",".join(
        sorted(settings.get("doctype_ids") or ([config.doctype_id] if config.doctype_id else []))
    )
    cache_key = f"{config.id}:{ids_sig}"
    if cache_key in _CLASSIFIER_CACHE:
        return _CLASSIFIER_CACHE[cache_key]

    vector_store, lexical_engine = _shared_models()
    doctypes = _load_doctypes(config)
    registry = CategoryRegistry(
        vector_store=vector_store, lexical_engine=lexical_engine, doctype_repository=doctypes
    )
    embedder = EdenAIEmbedder(embedding_config_from_config(config))
    llm = LLMClassifier(classifier_config_from_config(config))

    classifier = DocumentClassifier(
        lexical_engine=lexical_engine,
        embedder=embedder,
        llm=llm,
        vector_store=vector_store,
        category_registry=registry,
    )
    _CLASSIFIER_CACHE[cache_key] = classifier
    return classifier


def _persist_cost(tx_id, doc_id, account_id, source, amount) -> None:
    """Écrit une ligne de coût de classification (best-effort)."""
    if not amount:
        return
    now = datetime.now(UTC)
    with session_scope() as s:
        s.add(
            Cost(
                account_id=account_id or None,
                transaction_id=tx_id,
                document_id=doc_id,
                amount=amount,
                provider=source or "",
                model="",
                process=PROCESS_CLASSIFY,
                details=source or "",
                month=f"{now.month:02d}",
                year=str(now.year),
            )
        )


def classify_document(payload: dict) -> dict:
    """Classe le document et persiste le doctype. Renvoie le payload enrichi."""
    from alambic_core.pipeline.step import step

    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document") or {}
    doc_id = doc.get("documentId")
    config_id = payload.get("configId")
    account_id = payload.get("accountId")

    with step(tx_id, PROCESS_CLASSIFY, document_id=doc_id) as st:
        if st.skipped:
            return payload

        # Config + markdown OCR.
        with session_scope() as s:
            config = s.get(Config, config_id) if config_id else None
            if config is None:
                logger.warning("Classification : config %s introuvable, étape sautée", config_id)
                payload["classification"] = {"skipped": "no_config"}
                return payload
            d = s.get(Document, doc_id)
            text = (d.ocr_markdown if d is not None else "") or ""

        if not text:
            logger.info("Classification sautée (pas de texte OCR) pour %s", doc_id)
            payload["classification"] = {"skipped": "no_text"}
            return payload

        classifier = _get_classifier(config)
        result = classifier.classify_document(text)

        # Persistance du doctype identifié + champs (pour l'extraction).
        with session_scope() as s:
            d = s.get(Document, doc_id)
            if d is not None:
                d.doctype = result.type
                d.doctype_desc = result.description or ""

        # Trace du coût (uniquement si le LLM a été appelé).
        try:
            _persist_cost(tx_id, doc_id, account_id, result.source, result.cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Classification : coût non enregistré : %s", exc)

        payload["classification"] = {
            "type": result.type,
            "source": result.source,
            "confidence": result.confidence,
            "delta": result.delta,
            "cost": result.cost,
        }
        # Les champs identifiés voyagent vers l'extraction.
        payload["fields"] = result.fields
        logger.info(
            "Document %s classé : %s (source=%s, conf=%.2f)",
            doc_id,
            result.type,
            result.source,
            result.confidence,
        )

    return payload
