"""alambic_core.services.reclassification — reclassification manuelle d'un document.

Permet à un opérateur d'attribuer ou de corriger le doctype d'un document
(cas « unknown » non reconnu, ou classification automatique erronée). Après
reclassification, l'extraction doit être relancée avec le nouveau type.

Ce service ne fait que la partie CORE (mise à jour du document + garde-fous) ;
le déclenchement effectif de la tâche d'extraction (Celery) est fait par l'UI,
qui dispose du broker.
"""

from __future__ import annotations

import logging

from alambic_core.domain.enums import DocumentProcess, DocumentStatus
from alambic_core.models import Config, Doctype, Document

logger = logging.getLogger("alambic.reclassification")


def allowed_doctypes_for_document(session, doc: Document) -> list[Doctype]:
    """Doctypes proposables pour reclasser ce document : ceux déclarés dans la
    config du document (expected_doctypes), résolus en objets Doctype visibles.

    On se limite au périmètre de la config (choix produit), pas à tout le
    catalogue : reclasser vers un type hors config n'aurait pas de champs à
    extraire. L'option « laisser deviner » (let_it_guess) est gérée par l'UI.
    """
    tx = doc.transaction
    config = session.get(Config, tx.config_id) if tx and tx.config_id else None
    if config is None:
        return []

    expected = config.expected_doctypes or []
    wanted_ids = {
        (e.get("doctype_id") if isinstance(e, dict) else e) for e in expected
    }
    wanted_ids.discard(None)
    if not wanted_ids:
        # Config sans doctypes déclarés : proposer les doctypes visibles du compte.
        q = session.query(Doctype)
        if config.account_id:
            q = q.filter(
                (Doctype.account_id == config.account_id) | (Doctype.is_public.is_(True))
            )
        return q.order_by(Doctype.doctype_name).all()

    rows = session.query(Doctype).filter(Doctype.id.in_(wanted_ids)).all()
    return sorted(rows, key=lambda d: d.doctype_name or "")


def reclassify_document(session, doc_id: str, doctype_name: str) -> bool:
    """Attribue un doctype à un document et le prépare pour ré-extraction.

    - pose document.doctype = doctype_name (source de vérité de l'extraction) ;
    - remet le process à CLASSIFIER (juste avant FIELD_EXTRACTOR) pour que le
      step d'extraction s'exécute au lieu d'être sauté ;
    - remet un statut cohérent (sort de UNRECOGNIZED).

    Renvoie True si le document existe et a été mis à jour. Ne commit PAS
    (l'appelant gère la transaction).
    """
    doc = session.get(Document, doc_id)
    if doc is None:
        return False

    doc.doctype = doctype_name or ""
    # Repartir juste avant l'extraction : le step FIELD_EXTRACTOR ne doit pas
    # considérer l'étape « déjà passée » (sinon il la saute).
    doc.process = DocumentProcess.CLASSIFIER.value
    # Sortir de l'état non reconnu : le type est désormais fixé manuellement.
    if doc.status == DocumentStatus.UNRECOGNIZED.value:
        doc.status = DocumentStatus.OCR_DONE.value

    logger.info("Document %s reclassé manuellement en « %s »", doc_id, doctype_name)
    return True


def prepare_reclassify_guess(session, doc_id: str) -> bool:
    """Prépare un document à être RE-classifié automatiquement (« laisser deviner »).

    Remet le process avant CLASSIFIER pour que l'étape de classification
    s'exécute à nouveau, et sort le document de l'état non reconnu.
    """
    doc = session.get(Document, doc_id)
    if doc is None:
        return False
    # Repartir avant la classification (OCR déjà fait).
    doc.process = DocumentProcess.OCR_READER.value
    if doc.status == DocumentStatus.UNRECOGNIZED.value:
        doc.status = DocumentStatus.OCR_DONE.value
    logger.info("Document %s préparé pour re-classification automatique", doc_id)
    return True


def build_extract_payload(session, doc_id: str) -> dict | None:
    """Construit le payload minimal attendu par extract_fields pour ce document."""
    doc = session.get(Document, doc_id)
    if doc is None:
        return None
    tx = doc.transaction
    return {
        "transactionId": tx.id if tx else "",
        "transaction": {"transactionId": tx.id if tx else ""},
        "configId": tx.config_id if tx else "",
        "accountId": tx.account_id if tx else "",
        "document": {
            "documentId": doc.id,
            "file": {"bucket": doc.bucket_name, "key": doc.object_key},
            "source": "manual_reclassification",
        },
    }
