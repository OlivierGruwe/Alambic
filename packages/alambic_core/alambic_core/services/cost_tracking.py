"""alambic_core.services.cost_tracking — traçage unifié des coûts.

Objectif : qu'AUCUN coût n'échappe au suivi. Chaque étape IA (OCR, classification,
extraction) appelle `record_cost`, qui écrit systématiquement une ligne dans la
table Cost — même quand le montant est nul — afin de distinguer « pas d'appel » de
« appel à coût nul/non facturé » et d'avoir une trace exhaustive pour la projection
mensuelle.

Chaque enregistrement est aussi journalisé (process, document, montant, modèle,
durée) pour le suivi en direct dans les logs des workers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from alambic_core.db.session import session_scope
from alambic_core.models import Cost

logger = logging.getLogger("alambic.cost")


# Méthodes de classification (les 3 nœuds de la cascade). La source est au format
# "{méthode}_v{version}" (ex. "llm_vbootstrap", "embedding_v3", "lexical_v2").
CLASSIFICATION_METHODS = ("lexical", "embedding", "llm")


def classification_method(source: str) -> str:
    """Extrait la méthode de classification ("lexical"/"embedding"/"llm") d'une
    source au format "{méthode}_v{version}". Renvoie "" si non reconnu.

    Sert à distinguer la part GRATUITE (lexical + embedding, calcul local) de la
    part PAYANTE (llm, appel EdenAI) dans le suivi des coûts.
    """
    if not source:
        return ""
    head = str(source).split("_v", 1)[0].strip().lower()
    return head if head in CLASSIFICATION_METHODS else ""


def record_cost(
    *,
    process: str,
    amount: float,
    transaction_id: str | None = None,
    document_id: str | None = None,
    account_id: str | None = None,
    provider: str = "",
    model: str = "",
    source: str = "",
    duration_ms: int | None = None,
    pages: int | None = None,
    details: str = "",
) -> None:
    """Écrit une ligne de coût (toujours, même à 0) et journalise l'appel.

    `process` : OCR / CLASSIFY / EXTRACT (ou autre). `amount` : coût réel en €.
    `source` : sous-chemin (lexical/embedding/llm pour la classification, par ex.).
    `duration_ms` / `pages` : métadonnées utiles au coût unitaire (par page, par
    seconde), rangées dans `details` sous forme lisible.

    Best-effort : une erreur d'écriture ne doit jamais casser le pipeline.
    """
    now = datetime.now(UTC)
    amount = float(amount or 0.0)

    meta_parts = []
    if source:
        meta_parts.append(f"source={source}")
    if duration_ms is not None:
        meta_parts.append(f"{duration_ms}ms")
    if pages is not None:
        meta_parts.append(f"{pages}p")
    if details:
        meta_parts.append(details)
    meta = " ".join(meta_parts)

    # Journalisation systématique (suivi en direct).
    logger.info(
        "COST %s doc=%s tx=%s montant=%.6f€ provider=%s model=%s %s",
        process,
        document_id or "-",
        transaction_id or "-",
        amount,
        provider or source or "-",
        model or "-",
        meta,
    )

    try:
        with session_scope() as s:
            s.add(
                Cost(
                    account_id=account_id or None,
                    transaction_id=transaction_id,
                    document_id=document_id,
                    amount=amount,
                    provider=provider or "",
                    model=model or "",
                    process=process,
                    source=source or "",
                    details=meta,
                    month=f"{now.month:02d}",
                    year=str(now.year),
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Coût %s non enregistré pour doc=%s : %s", process, document_id, exc)
