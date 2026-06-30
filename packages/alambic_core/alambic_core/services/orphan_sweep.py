"""alambic_core.services.orphan_sweep — voiture-balai des dossiers Garage orphelins.

Filet de sécurité du stockage : supprime les dossiers de travail Garage dont la
transaction n'existe plus en base. Rattrape les suppressions partiellement
échouées (Garage qui ne supprime pas, panne réseau au mauvais moment) et les
fichiers laissés par d'anciens bugs.

Parcours : __transactions__/<account_id>/<config_id>/<transaction_id>/
Pour chaque transaction_id trouvé dans Garage, on vérifie son existence en base.
Si elle n'existe plus → le dossier est orphelin → suppression du préfixe.

PRUDENCE : opération destructive. Le mode dry-run (défaut) liste les orphelins
sans rien supprimer, pour inspection avant un vrai passage. Le backup
(__backup__/) n'est jamais touché (il est hors du préfixe __transactions__).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import storage
from ..db.session import session_scope
from ..models import Transaction

logger = logging.getLogger(__name__)

WORK_PREFIX = "__transactions__"


@dataclass
class OrphanSweepResult:
    """Compte rendu d'un passage de la voiture-balai."""

    scanned: int = 0
    orphans: list[str] = field(default_factory=list)  # préfixes orphelins
    deleted_prefixes: int = 0
    files_deleted: int = 0
    dry_run: bool = True


def _iter_transaction_prefixes(bucket: str):
    """Énumère (transaction_id, prefix) pour chaque dossier de transaction Garage.

    Hiérarchie : __transactions__/<account>/<config>/<trx>/. On descend les trois
    niveaux via les CommonPrefixes (sans lister tous les objets).
    """
    root = f"{WORK_PREFIX}/"
    for acc_prefix in storage.list_common_prefixes(bucket, root):
        for cfg_prefix in storage.list_common_prefixes(bucket, acc_prefix):
            for trx_prefix in storage.list_common_prefixes(bucket, cfg_prefix):
                # trx_prefix = __transactions__/<account>/<config>/<trx>/
                trx_id = trx_prefix.rstrip("/").split("/")[-1]
                yield trx_id, trx_prefix


def sweep_orphans(*, dry_run: bool = True, work_bucket: str | None = None) -> OrphanSweepResult:
    """Détecte (et supprime si dry_run=False) les dossiers Garage orphelins.

    Un dossier est orphelin si son transaction_id n'existe plus en base.
    En dry-run (défaut), liste les orphelins sans supprimer.
    """
    bucket = work_bucket or storage.work_bucket()
    result = OrphanSweepResult(dry_run=dry_run)

    # 1) Collecte les (trx_id, prefix) présents dans Garage.
    found = list(_iter_transaction_prefixes(bucket))
    result.scanned = len(found)
    if not found:
        return result

    # 2) Vérifie en une requête quels trx_id existent encore en base.
    trx_ids = list({tid for tid, _ in found})
    with session_scope() as s:
        existing = {
            row[0] for row in s.query(Transaction.id).filter(Transaction.id.in_(trx_ids)).all()
        }

    # 3) Les préfixes dont le trx_id n'existe plus sont orphelins.
    for trx_id, prefix in found:
        if trx_id in existing:
            continue
        result.orphans.append(prefix)
        if not dry_run:
            n = storage.delete_prefix(bucket, prefix)
            result.deleted_prefixes += 1
            result.files_deleted += n
            logger.info("Voiture-balai : dossier orphelin supprimé %s (%d fichiers)", prefix, n)
        else:
            logger.info("Voiture-balai (dry-run) : dossier orphelin détecté %s", prefix)

    logger.info(
        "Voiture-balai : %d dossier(s) scanné(s), %d orphelin(s)%s",
        result.scanned,
        len(result.orphans),
        "" if dry_run else f", {result.deleted_prefixes} supprimé(s)",
    )
    return result
