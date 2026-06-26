"""alambic_workers.tasks.retry — relance d'une transaction bloquée.

Réinjecte dans la chaîne de traitement les documents d'une transaction qui n'ont
pas atteint un état terminal (typiquement une transaction restée WORKING trop
longtemps à cause d'une étape qui a échoué silencieusement ou d'un worker tombé).

Reconstruit pour chaque document non terminé le même payload que l'ingestion
initiale et le renvoie à run_processing (début de la chaîne : conversion → cab →
ocr → split → classif → extract → finalize).
"""

from __future__ import annotations

import logging

from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentStatus
from alambic_core.models import Document, Transaction

logger = logging.getLogger(__name__)

# Documents qu'on ne relance pas : terminés ou hors-jeu.
_DONE_STATUSES = {
    DocumentStatus.VALIDATED.value,
    DocumentStatus.EXPORTED.value,
    DocumentStatus.DISCARDED.value,
    DocumentStatus.DEPRECATED.value,
}


def retry_transaction(transaction_id: str) -> dict:
    """Relance les documents non terminés d'une transaction.

    Renvoie {relaunched: N, transaction_id}. N = nombre de documents réinjectés.
    """
    from alambic_workers.orchestration.processing import run_processing

    with session_scope() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is None:
            return {
                "relaunched": 0,
                "transaction_id": transaction_id,
                "error": "transaction_introuvable",
            }

        config_id = tx.config_id
        account_id = tx.account_id

        docs = s.query(Document).filter(Document.transaction_id == transaction_id).all()
        to_relaunch = [
            d
            for d in docs
            if d.status not in _DONE_STATUSES and d.object_key  # besoin du fichier source
        ]

        payloads = [
            {
                "transactionId": transaction_id,
                "configId": config_id,
                "accountId": account_id,
                "document": {
                    "documentId": d.id,
                    "file": {"bucket": d.bucket_name, "key": d.object_key},
                },
                "process": "PROCESSING",
            }
            for d in to_relaunch
        ]

    # Réinjection hors session (les tâches Celery ouvriront la leur).
    for p in payloads:
        run_processing.apply_async(args=[p], queue="normal")

    logger.info(
        "Transaction %s relancée : %d document(s) réinjecté(s)", transaction_id, len(payloads)
    )
    return {"relaunched": len(payloads), "transaction_id": transaction_id}
