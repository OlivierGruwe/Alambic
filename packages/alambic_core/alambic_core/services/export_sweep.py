"""alambic_core.services.export_sweep — rattrapage des exports en attente.

Filet de sécurité : un document validé dont l'export a échoué (broker
indisponible, web service momentanément down, timeout réseau) repasse en
VALIDATED — réexportable, mais rien ne le relance automatiquement. Ce balayage,
exécuté périodiquement par Celery Beat, rattrape ces documents.

Point clé : la destination d'export vit dans la Config et peut changer dans le
temps (un admin configure ou corrige way_out / URL / credentials après coup). Le
balayage RELIT donc la Config à chaque passage — il ne se fie jamais à un état
figé. Un document validé sans destination hier mais configuré aujourd'hui est
rattrapé au prochain tour.

Ce module sélectionne les candidats et vérifie qu'une destination existe
*maintenant* ; l'export effectif (et la re-relecture de la Config au moment
d'exporter) est délégué à l'appelant via un callback, pour ne pas créer de
dépendance du core vers les workers.
"""

from __future__ import annotations

import logging

from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentStatus
from alambic_core.models import Config, Document

logger = logging.getLogger(__name__)


def find_pending_exports(limit: int = 200) -> list[str]:
    """Renvoie les IDs des documents VALIDATED à (ré)exporter.

    Un document est candidat si :
      - son statut est VALIDATED (validé mais pas encore exporté) ;
      - la Config de sa transaction définit une destination (way_out non vide),
        relue à chaque appel pour rester à jour.

    Les documents sans destination configurée sont ignorés (ils ne seraient
    jamais exportés — inutile de boucler dessus).
    """
    pending = []
    with session_scope() as s:
        docs = (
            s.query(Document)
            .filter(Document.status == DocumentStatus.VALIDATED.value)
            .limit(limit)
            .all()
        )
        # Cache local des configs déjà évaluées dans CE passage (évite de relire
        # N fois la même config), mais rechargé à chaque exécution du balayage.
        config_has_dest: dict[str, bool] = {}
        for d in docs:
            tx = d.transaction
            config_id = tx.config_id if tx is not None else None
            if not config_id:
                continue
            if config_id not in config_has_dest:
                config = s.get(Config, config_id)
                settings = (config.edenai_settings or {}) if config is not None else {}
                config_has_dest[config_id] = bool(settings.get("way_out"))
            if config_has_dest[config_id]:
                pending.append(d.id)

    logger.info("Balayage export : %d document(s) en attente d'export", len(pending))
    return pending


def sweep_exports(export_fn, limit: int = 200) -> dict:
    """Rattrape les exports en attente en appelant export_fn(doc_id) pour chacun.

    `export_fn` est la fonction d'export réelle (qui relit la Config fraîche au
    moment d'exporter et gère le statut). Renvoie un résumé {scanned, exported,
    failed}.
    """
    doc_ids = find_pending_exports(limit=limit)
    exported, failed = [], []
    for doc_id in doc_ids:
        try:
            result = export_fn(doc_id)
            if result and result.get("ok"):
                exported.append(doc_id)
            else:
                failed.append(doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Balayage export : échec sur %s : %s", doc_id, exc)
            failed.append(doc_id)

    return {
        "scanned": len(doc_ids),
        "exported": len(exported),
        "failed": len(failed),
    }
