"""alambic_core.pipeline.step — exécution encadrée d'une étape du pipeline.

Factorise les deux invariants exigés pour CHAQUE étape :

1. MAJ DB systématique : marque le process courant + process_time, et écrit une
   ligne de journal (TransactionStep) avec horodatages et durée.
2. Rejouabilité : si l'entité (document, ou transaction) est déjà au-delà de
   l'étape, on saute (skip) — ré-exécuter ne corrompt rien.

Usage :

    with step(tx_id, "FILEEXTRACTOR", document_id=doc_id) as st:
        if st.skipped:
            return            # déjà fait, rien à refaire
        ...  # le vrai travail de l'étape

À la sortie normale : process/process_time mis à jour, ligne de journal OK.
En cas d'exception : ligne de journal ERROR + message sur la transaction, et
l'exception est propagée (l'orchestrateur décide quoi en faire).

Le suivi FIN (process/process_time + skip) se fait au niveau DOCUMENT quand
document_id est fourni — chaque document avance à son rythme. Sinon au niveau
transaction (étapes globales : NEWDOC, DISPATCH_DONE…).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from ..db.session import session_scope
from ..models import Document, Message, Transaction, TransactionStep
from .steps import is_already_past

logger = logging.getLogger(__name__)

SOURCE = "pipeline"


class TransientStepError(Exception):
    """Échec transitoire d'une étape (dépendance externe momentanément indisponible).

    Levée pour signaler que l'étape n'a pas pu aboutir à cause d'une panne
    externe (ex. LLM/EdenAI injoignable), et qu'il faut réessayer plus tard.
    Le step la propage SANS marquer le document en échec ni avancer son process,
    pour qu'il reste dans l'état d'avant l'étape et soit rejouable.
    """


@dataclass
class StepContext:
    """État d'une étape en cours, exposé dans le bloc `with`."""

    transaction_id: str
    process: str
    document_id: str | None = None
    skipped: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def step(
    transaction_id: str,
    process: str,
    *,
    document_id: str | None = None,
) -> Iterator[StepContext]:
    """Encadre une étape : skip si déjà passée, sinon exécute, journalise, MAJ DB.

    Voir le docstring du module pour l'usage et la sémantique.
    """
    ctx = StepContext(transaction_id=transaction_id, process=process, document_id=document_id)

    # ── Skip de rejouabilité ────────────────────────────────────────────────
    # On regarde le process courant de l'entité suivie (document si fourni,
    # sinon transaction). Si elle est déjà au-delà de `process`, on saute.
    with session_scope() as s:
        current = _current_process(s, transaction_id, document_id)
    if current is not None and is_already_past(current, process):
        logger.info(
            "step %s sautée (déjà au-delà : %s) tx=%s doc=%s",
            process,
            current,
            transaction_id,
            document_id,
        )
        ctx.skipped = True
        yield ctx
        return

    started_at = _now()
    try:
        yield ctx
    except TransientStepError:
        # Panne externe transitoire (ex. LLM injoignable) : ce n'est PAS un échec
        # du document. On ne journalise pas d'ERROR, on ne touche pas au process
        # du document (il reste à réessayer), et on propage pour que l'appelant
        # (tâche Celery) puisse relancer.
        logger.warning(
            "step %s différée (panne transitoire) tx=%s doc=%s",
            process,
            transaction_id,
            document_id,
        )
        raise
    except Exception as exc:
        # Journalise l'échec + message sur la transaction, puis propage.
        logger.exception("step %s en échec tx=%s doc=%s", process, transaction_id, document_id)
        ended_at = _now()
        with session_scope() as s:
            s.add(
                TransactionStep(
                    transaction_id=transaction_id,
                    document_id=document_id,
                    process=process,
                    status="ERROR",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=_duration_ms(started_at, ended_at),
                    detail=str(exc)[:2000],
                )
            )
            doc_ref = f" (document {document_id})" if document_id else ""
            s.add(
                Message(
                    transaction_id=transaction_id,
                    level="ERROR",
                    source=SOURCE,
                    text=f"Étape {process} en échec{doc_ref} : {exc}",
                )
            )
        raise
    else:
        # Succès (hors skip déjà géré au-dessus).
        ended_at = _now()
        with session_scope() as s:
            # Journal de l'étape.
            s.add(
                TransactionStep(
                    transaction_id=transaction_id,
                    document_id=document_id,
                    process=process,
                    status="OK",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=_duration_ms(started_at, ended_at),
                )
            )
            # MAJ de l'état courant : document si fourni, SINON transaction.
            # Important : on ne met PAS à jour la transaction pour une étape
            # par-document. Plusieurs documents de la même transaction franchissent
            # une étape EN PARALLÈLE (workers distincts) ; écrire tous sur la même
            # ligne transaction déclenche un conflit de version optimiste
            # (StaleDataError). L'état transaction est suivi par les étapes de
            # niveau transaction (NEWDOC, FILEEXTRACTOR…), sans document_id.
            if document_id is not None:
                doc = s.get(Document, document_id)
                if doc is not None:
                    doc.process = process
                    doc.process_time = ended_at
            else:
                tx = s.get(Transaction, transaction_id)
                if tx is not None:
                    tx.process = process
                    tx.process_time = ended_at


def _current_process(s, transaction_id: str, document_id: str | None) -> str | None:
    """Process courant de l'entité suivie (document si fourni, sinon transaction)."""
    if document_id is not None:
        doc = s.get(Document, document_id)
        return doc.process if doc is not None else None
    tx = s.get(Transaction, transaction_id)
    return tx.process if tx is not None else None


def _duration_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))
