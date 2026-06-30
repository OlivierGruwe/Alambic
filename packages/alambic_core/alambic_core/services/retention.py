"""alambic_core.services.retention — purge des transactions après rétention.

Une transaction exportée avec succès reste consultable dans l'UI (historique,
fichiers de travail accessibles pour vérification) pendant une durée de
rétention. Passé ce délai, elle est purgée intégralement (fichiers work Garage
+ ligne en base via cascade), en un seul temps. Le backup est conservé.

Le délai de rétention est défini PAR CONFIG (chaque client le sien), via la clé
`retention_days` du bloc JSONB `general` de la config. Repli sur une valeur
globale (ALAMBIC_RETENTION_DAYS, défaut 30 jours) si la config ne le précise pas.

L'éligibilité repose sur `exported_at` : une transaction est purgeable si
exported_at + retention_days < maintenant.

Pensé pour être appelé par un job planifié (Celery Beat, balayage quotidien).
"""

from __future__ import annotations

import datetime as dt
import logging
import os

from ..db.session import session_scope
from ..models import Config, Transaction
from .deletion import DeletionResult, delete_transaction

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30


def global_retention_days() -> int:
    """Délai de rétention global par défaut (env ALAMBIC_RETENTION_DAYS)."""
    raw = os.environ.get("ALAMBIC_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("ALAMBIC_RETENTION_DAYS invalide (%r), repli défaut", raw)
        return DEFAULT_RETENTION_DAYS


def config_retention_days(config: Config | None) -> int:
    """Délai de rétention d'une config (general.retention_days), sinon global."""
    if config is not None:
        general = config.general or {}
        value = general.get("retention_days")
        if value is not None:
            try:
                return max(0, int(value))
            except (ValueError, TypeError):
                logger.warning(
                    "retention_days invalide pour config %s (%r), repli global",
                    config.id,
                    value,
                )
    return global_retention_days()


def find_purgeable_transactions(now: dt.datetime | None = None) -> list[str]:
    """Renvoie les ids des transactions exportées dont la rétention est écoulée.

    Une transaction est éligible si :
    - elle a un statut EXPORTED (export réussi) ;
    - elle a une date exported_at ;
    - exported_at + retention_days (de sa config) < now.
    """
    now = now or dt.datetime.now(dt.UTC)
    eligible: list[str] = []

    with session_scope() as s:
        # On ne charge que les transactions exportées et datées.
        rows = (
            s.query(Transaction)
            .filter(Transaction.status == "EXPORTED")
            .filter(Transaction.exported_at.isnot(None))
            .all()
        )
        for tx in rows:
            days = config_retention_days(tx.config)
            exported = tx.exported_at
            # SQLite ne conserve pas le fuseau : on normalise en UTC-aware pour
            # comparer sans erreur (Postgres renvoie déjà de l'aware).
            if exported.tzinfo is None:
                exported = exported.replace(tzinfo=dt.UTC)
            deadline = exported + dt.timedelta(days=days)
            if deadline < now:
                eligible.append(tx.id)

    return eligible


def purge_expired_transactions(
    now: dt.datetime | None = None,
) -> list[DeletionResult]:
    """Purge toutes les transactions dont la rétention est écoulée.

    Renvoie la liste des DeletionResult. Chaque purge est indépendante : une
    erreur sur l'une n'empêche pas les autres (loguée, on continue).
    """
    ids = find_purgeable_transactions(now=now)
    results: list[DeletionResult] = []
    for tx_id in ids:
        try:
            results.append(delete_transaction(tx_id))
        except Exception:
            logger.exception("Purge échouée pour la transaction %s", tx_id)
    if results:
        logger.info("Purge de rétention : %d transaction(s) supprimée(s)", len(results))
    return results
