"""alambic_workers.tasks.retention — purge planifiée des transactions exportées.

Tâche balayée quotidiennement par Celery Beat. Elle délègue toute la logique au
service alambic_core.services.retention : recherche des transactions dont la
rétention (par config, repli global) est écoulée, puis purge complète (fichiers
work Garage + base via cascade ; backup conservé).

La planification est définie dans celery_app (beat_schedule).
"""

from __future__ import annotations

import logging

from alambic_core.services import purge_expired_transactions

from alambic_workers.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="alambic_workers.retention.purge", bind=True)
def purge_retention(self) -> dict:
    """Purge les transactions dont la rétention est écoulée. Renvoie un résumé."""
    results = purge_expired_transactions()
    purged = [r.transaction_id for r in results]
    files = sum(r.files_deleted for r in results)
    logger.info(
        "Purge de rétention : %d transaction(s), %d fichier(s) supprimé(s)",
        len(purged),
        files,
    )
    return {"purged": purged, "count": len(purged), "files_deleted": files}


@app.task(name="alambic_workers.export.sweep", bind=True)
def sweep_pending_exports(self) -> dict:
    """Rattrape les documents validés en attente d'export (filet de sécurité).

    Balayé périodiquement par Celery Beat. Relit la Config de chaque transaction
    (la destination peut avoir été configurée/corrigée après coup) et réexporte
    les documents VALIDATED dont une destination existe. Délègue l'export réel à
    export_document (qui relit lui aussi la Config fraîche et gère le statut).
    """
    from alambic_core.services.export_sweep import sweep_exports

    from alambic_workers.tasks.export import export_document

    summary = sweep_exports(export_document)
    logger.info(
        "Balayage export : %d scanné(s), %d exporté(s), %d échec(s)",
        summary["scanned"],
        summary["exported"],
        summary["failed"],
    )
    return summary


@app.task(name="alambic_workers.storage.orphan_sweep", bind=True)
def sweep_orphan_folders(self, dry_run: bool = False) -> dict:
    """Voiture-balai : supprime les dossiers Garage dont la transaction n'existe plus.

    Balayée périodiquement par Celery Beat (ménage de fond). Rattrape les
    suppressions partiellement échouées et les fichiers orphelins. Ne supprime
    que des dossiers confirmés orphelins (transaction absente de la base).
    """
    from alambic_core.services.orphan_sweep import sweep_orphans

    result = sweep_orphans(dry_run=dry_run)
    logger.info(
        "Voiture-balai Garage : %d scanné(s), %d orphelin(s), %d supprimé(s), %d fichier(s)",
        result.scanned,
        len(result.orphans),
        result.deleted_prefixes,
        result.files_deleted,
    )
    return {
        "scanned": result.scanned,
        "orphans": len(result.orphans),
        "deleted_prefixes": result.deleted_prefixes,
        "files_deleted": result.files_deleted,
        "dry_run": result.dry_run,
    }


@app.task(name="alambic_workers.vectors.compact", bind=True)
def compact_vectors(self) -> dict:
    """Agrège les embeddings des documents validés en centroïdes de classification.

    Balayée périodiquement par Celery Beat. Lit les nouveaux logs vectoriels
    (écrits à chaque validation humaine) depuis un curseur et met à jour le
    modèle de centroïdes, ce qui affine la classification par embedding et
    réduit le recours au LLM.
    """
    from alambic_core.services.vector_compactor import compact

    result = compact()
    logger.info(
        "Compaction vectorielle : %s (version=%s, %s log(s))",
        result.get("status"),
        result.get("version"),
        result.get("logs"),
    )
    return result
