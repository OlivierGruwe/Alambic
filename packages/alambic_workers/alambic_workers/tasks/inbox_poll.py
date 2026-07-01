"""alambic_workers.tasks.inbox_poll — import périodique des entrées FTP/S3.

Balayée par Celery Beat (toutes les 5 min). Pour chaque Config active dont
l'entrée est FTP ou S3 :
  1. construit le connecteur (FtpConnector / S3Connector) depuis la config ;
  2. liste les fichiers présents dans la source ;
  3. pour chaque fichier NON vu récemment (dédoublonnage temporel, fenêtre
     glissante), télécharge les octets, les dépose dans Garage et déclenche
     l'ingestion (origine « FTP » ou « S3 »), puis déplace le fichier source
     vers treated/YYYYMMDD/ et enregistre l'import dans le registre.

Robustesse :
- une config en échec (source injoignable) ne bloque pas les autres ;
- le registre (dédoublonnage) est mis à jour APRÈS ingestion réussie, et le
  déplacement vers treated/ n'est tenté qu'ensuite : un déplacement échoué ne
  perd pas le fichier (il sera re-vu au prochain cycle, mais le registre évite le
  double-import dans la fenêtre).
"""

from __future__ import annotations

import logging
import os
import tempfile

from alambic_core import storage
from alambic_core.db.session import session_scope
from alambic_core.ingest import connector_from_config, way_in_of
from alambic_core.models import Config
from alambic_core.services.ingest_dedup import (
    DEFAULT_WINDOW_MINUTES,
    record_import,
    should_import,
)

from alambic_workers.celery_app import app
from alambic_workers.tasks.start_ingestion import start_ingestion

logger = logging.getLogger(__name__)

INPUT_BUCKET = os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input")


def _dedup_window(config) -> int:
    """Fenêtre de dédoublonnage (minutes) : configurable par config, sinon défaut."""
    ws = getattr(config, "ws", None) or {}
    try:
        val = int(ws.get("ingest_dedup_minutes") or DEFAULT_WINDOW_MINUTES)
        return val if val > 0 else DEFAULT_WINDOW_MINUTES
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINUTES


def _ingest_one_file(config, connector, source_key: str, work_dir: str) -> str | None:
    """Télécharge un fichier source et déclenche l'ingestion. Renvoie la clé
    d'upload Garage si lancée, None sinon."""
    import posixpath

    filename = posixpath.basename(source_key) or "fichier"
    data = connector.fetch(source_key)

    object_key = storage.build_upload_key(
        config.account_id or "",
        config.id or "",
        filename,
        origin=connector.source_type,  # « FTP » ou « S3 »
    )
    local_path = os.path.join(work_dir, filename)
    with open(local_path, "wb") as fh:
        fh.write(data)
    storage.put_object(INPUT_BUCKET, object_key, local_path)

    start_ingestion(
        bucket=INPUT_BUCKET,
        object_key=object_key,
        local_path=local_path,
        original_filename=filename,
    )
    return object_key


def _poll_one(config) -> dict:
    """Balaie une config : liste, dédoublonne, ingère, déplace, enregistre."""
    res = {"listed": 0, "ingested": 0, "skipped": 0, "moved": 0}
    connector = connector_from_config(config)
    if connector is None:
        return res

    files = connector.list_files()
    res["listed"] = len(files)
    window = _dedup_window(config)
    work_dir = tempfile.mkdtemp(prefix="alambic_inbox_")

    for source_key in files:
        # 1. Dédoublonnage temporel : ne pas ré-importer un fichier vu récemment.
        with session_scope() as s:
            if not should_import(s, config.id, source_key, window_minutes=window):
                res["skipped"] += 1
                continue

        # 2. Ingestion.
        try:
            upload_key = _ingest_one_file(config, connector, source_key, work_dir)
        except Exception:
            logger.exception("Inbox %s : ingestion de %s en échec", config.id, source_key)
            continue

        # 3. Enregistrer l'import (dédoublonnage) APRÈS ingestion réussie.
        with session_scope() as s:
            record_import(
                s, config.id, source_key,
                source_type=connector.source_type, upload_key=upload_key or "",
            )
        res["ingested"] += 1

        # 4. Déplacer le fichier source vers treated/YYYYMMDD/ (best effort : un
        #    échec ne perd pas le fichier, le registre couvre le double-import).
        try:
            connector.move_to_treated(source_key)
            res["moved"] += 1
        except Exception:
            logger.warning(
                "Inbox %s : déplacement de %s vers treated/ échoué (registre couvre)",
                config.id, source_key,
            )

    return res


@app.task(name="alambic_workers.inbox.poll", bind=True)
def poll_inboxes(self) -> dict:  # noqa: ARG001
    """Importe les fichiers de toutes les configs actives dont l'entrée est
    FTP ou S3 (tâche Celery Beat). Une config en échec ne bloque pas les autres."""
    summary = {"configs": 0, "listed": 0, "ingested": 0, "skipped": 0, "moved": 0, "errors": 0}

    with session_scope() as s:
        configs = s.query(Config).filter_by(is_active=True).all()
        # Ne garder que les configs avec entrée FTP/S3 ; détacher pour le réseau.
        configs = [c for c in configs if way_in_of(c) in ("FTP", "S3")]
        for c in configs:
            _ = (c.ws, c.ftp_in_enc, c.aws_in_enc, c.account_id)  # force le chargement

    for config in configs:
        summary["configs"] += 1
        try:
            r = _poll_one(config)
            summary["listed"] += r["listed"]
            summary["ingested"] += r["ingested"]
            summary["skipped"] += r["skipped"]
            summary["moved"] += r["moved"]
        except Exception:
            summary["errors"] += 1
            logger.exception("Inbox : balayage de la config %s en échec", config.id)

    logger.info(
        "Inbox poll : %d config(s), %d listé(s), %d ingéré(s), %d ignoré(s), %d déplacé(s)",
        summary["configs"], summary["listed"], summary["ingested"],
        summary["skipped"], summary["moved"],
    )
    return summary
