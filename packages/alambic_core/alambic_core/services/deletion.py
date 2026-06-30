"""alambic_core.services.deletion — suppression d'une transaction et de ses dépendances.

Supprime une transaction de façon complète et sûre :

1. Stockage Garage d'ABORD : tous les fichiers de travail de la transaction
   (préfixe __transactions__/<account>/<config>/<trx_id>/). Le backup
   (__backup__/) est CONSERVÉ — c'est la source de récupération.
2. Base ENSUITE : suppression de la transaction. Les cascades SQL
   (ondelete=CASCADE) effacent automatiquement documents, document_indexes,
   transaction_steps (history), messages et costs.

Ordre Garage→base voulu : si Garage échoue, la transaction reste en base
(intacte, ré-essayable). Si on supprimait la base d'abord et que Garage échouait,
on perdrait l'info pour retrouver les fichiers → orphelins définitifs.

Réutilisable par l'UI, un worker, ou un CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import storage
from ..db.session import session_scope
from ..models import Transaction

logger = logging.getLogger(__name__)

WORK_PREFIX = "__transactions__"


@dataclass
class DeletionResult:
    """Compte rendu d'une suppression."""

    transaction_id: str
    files_deleted: int
    found: bool


def transaction_work_prefix(tx: Transaction) -> str:
    """Préfixe Garage des fichiers de travail d'une transaction.

    __transactions__/<account_id>/<config_id>/<transaction_id>/
    Tout (source, PDF convertis, documents enfants) vit sous ce préfixe.
    """
    return f"{WORK_PREFIX}/{tx.account_id}/{tx.config_id}/{tx.id}/"


def delete_transaction(transaction_id: str, *, work_bucket: str | None = None) -> DeletionResult:
    """Supprime une transaction : fichiers de travail Garage puis base (cascade).

    Le backup (__backup__/) n'est PAS touché. Renvoie un DeletionResult.
    Idempotent : supprimer une transaction inexistante renvoie found=False sans
    erreur.
    """
    bucket = work_bucket or storage.work_bucket()

    # ── Lecture des coordonnées (sans garder la session ouverte pendant l'I/O) ─
    with session_scope() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is None:
            logger.info("delete_transaction : %s introuvable", transaction_id)
            return DeletionResult(transaction_id, files_deleted=0, found=False)
        prefix = transaction_work_prefix(tx)

    # ── 1. Garage d'abord (work uniquement, backup conservé) ─────────────────
    # On le fait HORS session DB : c'est de l'I/O réseau, inutile de tenir un
    # verrou de transaction pendant ce temps.
    files_deleted = storage.delete_prefix(bucket, prefix)
    logger.info(
        "delete_transaction %s : %d fichier(s) supprimé(s) sous %s",
        transaction_id,
        files_deleted,
        prefix,
    )

    # ── 2. Base ensuite (cascade efface documents, steps, messages, costs…) ──
    with session_scope() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is not None:
            s.delete(tx)

    return DeletionResult(transaction_id, files_deleted=files_deleted, found=True)
