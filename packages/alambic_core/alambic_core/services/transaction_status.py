"""alambic_core.services.transaction_status — statut consolidé d'une transaction.

Porté de FlowerScan (helpers/transactions._compute_transaction_status), adapté
aux statuts de document d'Alambic.

Le statut affiché d'une transaction est recalculé depuis l'état réel de ses
documents non-dépréciés, plutôt que lu brut : il propage une erreur dès qu'un
document échoue, signale « en cours » tant qu'un document n'a pas atteint un
état terminal, et « terminé » quand tous le sont. Cela garde l'affichage juste
sans attendre qu'un job mette à jour le champ status de la transaction.
"""

from __future__ import annotations

from alambic_core.domain.enums import DocumentStatus

# Documents exclus du comptage actif et du calcul de statut.
NON_ACTIVE_DOC_STATUSES = {DocumentStatus.DEPRECATED.value, DocumentStatus.DISCARDED.value}

# États terminaux d'un document (plus de traitement attendu).
TERMINAL_DOC_STATUSES = {
    DocumentStatus.PENDING_VALIDATION.value,
    DocumentStatus.VALIDATED.value,
    DocumentStatus.EXPORTED.value,
    DocumentStatus.FAILED.value,
}

# États d'erreur.
ERROR_DOC_STATUSES = {DocumentStatus.FAILED.value}


def active_documents(documents: list) -> list:
    """Filtre les documents actifs (exclut DEPRECATED/DISCARDED)."""
    return [d for d in documents if _status_of(d) not in NON_ACTIVE_DOC_STATUSES]


def _status_of(doc) -> str:
    """Statut d'un document, qu'il soit un objet ORM ou un dict."""
    if isinstance(doc, dict):
        return doc.get("status", "")
    return getattr(doc, "status", "")


def compute_transaction_status(tx_status: str, documents: list) -> str:
    """Réconcilie le statut d'une transaction avec l'état de ses documents.

    Priorité : erreur > en cours > tous terminés. Si pas de document actif
    (ex. découpage en cours), conserve le statut courant de la transaction.

    Ne fait jamais régresser un statut terminal vers « en cours ».
    """
    actives = active_documents(documents)
    if not actives:
        return tx_status

    statuses = {_status_of(d) for d in actives}

    # 1. Au moins un échec → erreur.
    if statuses & ERROR_DOC_STATUSES:
        return DocumentStatus.FAILED.value

    # 2. Des documents encore en traitement → en cours.
    in_progress = statuses - TERMINAL_DOC_STATUSES
    if in_progress:
        return "WORKING"

    # 3. Tous exportés → exporté.
    if statuses <= {DocumentStatus.EXPORTED.value}:
        return DocumentStatus.EXPORTED.value

    # 4. Au moins un document en attente de validation humaine → en attente.
    if DocumentStatus.PENDING_VALIDATION.value in statuses:
        return DocumentStatus.PENDING_VALIDATION.value

    # 5. Tous validés (ou exportés) → validé.
    if statuses <= {DocumentStatus.VALIDATED.value, DocumentStatus.EXPORTED.value}:
        return DocumentStatus.VALIDATED.value

    # Cas imprévu : conserver le statut courant.
    return tx_status


def count_active_documents(documents: list) -> int:
    """Nombre de documents actifs (hors DEPRECATED/DISCARDED)."""
    return len(active_documents(documents))


def is_in_progress(status: str) -> bool:
    """Vrai si le statut indique un traitement en cours (utile au SSE)."""
    return status not in {
        DocumentStatus.VALIDATED.value,
        DocumentStatus.EXPORTED.value,
        DocumentStatus.FAILED.value,
    }
