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
