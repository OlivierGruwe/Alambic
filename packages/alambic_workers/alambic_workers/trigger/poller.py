"""alambic_workers.trigger.poller — déclencheur par scrutation de Garage.

Maillon entre le dépôt d'un fichier (zone __uploads__) et le pipeline. Remplace
la notification événementielle S3/EventBridge d'AWS par un polling simple, adapté
à une infra self-hosted (Garage n'émet pas d'événements S3 nativement).

Boucle : liste __uploads__/ dans le bucket d'entrée, et pour chaque fichier :
  1. télécharge le fichier en local (start_ingestion attend un chemin local) ;
  2. appelle start_ingestion (crée la transaction + pousse vers work/backup +
     lance le workflow Celery) ;
  3. supprime le fichier de __uploads__ pour ne pas le re-déclencher.

L'idempotence par transaction_key protège des doublons même si une suppression
échoue (un re-déclenchement retombera sur la transaction existante et sera skippé).

Usage :
    python -m alambic_workers.trigger.poller            # boucle (intervalle 5s)
    python -m alambic_workers.trigger.poller --once      # un seul passage
    python -m alambic_workers.trigger.poller --interval 10
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
import time

from alambic_core import storage

from alambic_workers.tasks.start_ingestion import start_ingestion

logger = logging.getLogger("alambic.poller")

UPLOAD_PREFIX = "__uploads__/"


def scan_once() -> int:
    """Un passage : déclenche chaque fichier présent sous __uploads__/.

    Renvoie le nombre de fichiers déclenchés.
    """
    bucket = os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input")
    objects = storage.list_objects(bucket, prefix=UPLOAD_PREFIX)
    triggered = 0

    for obj in objects:
        key = obj["key"]
        # Ignore les "dossiers" (clés se terminant par /) et la racine du préfixe.
        if key.endswith("/"):
            continue

        tmp_dir = tempfile.mkdtemp(prefix="alambic_poll_")
        local_path = os.path.join(tmp_dir, os.path.basename(key))
        try:
            storage.download_to(bucket, key, local_path)
            result = start_ingestion(
                bucket=bucket,
                object_key=key,
                local_path=local_path,
            )
            if result:
                logger.info(
                    "Déclenché : %s → transaction %s",
                    key,
                    result.get("transactionId"),
                )
                triggered += 1
            else:
                logger.info("Ignoré (doublon ou rejeté) : %s", key)

            # Retire le fichier de __uploads__ : il est désormais pris en charge
            # (copié vers work/backup par start_ingestion). On évite de le
            # re-déclencher au prochain passage.
            storage.delete_object(bucket, key)
        except Exception:
            logger.exception("Échec du déclenchement pour %s", key)
        finally:
            # Nettoyage du temporaire local.
            try:
                if os.path.exists(local_path):
                    os.unlink(local_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    return triggered


def run(interval: float = 5.0, once: bool = False) -> None:
    """Boucle de scrutation. interval = secondes entre deux passages."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Indispensable : le poller appelle start_ingestion, qui utilise
    # session_scope() (moteur SQLAlchemy global). Comme le worker Celery,
    # on initialise alambic_core au démarrage (sinon « Core non initialisé »).
    from alambic_core.db.session import init_core

    init_core()

    logger.info(
        "Poller démarré (bucket=%s, prefix=%s, intervalle=%ss)",
        os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input"),
        UPLOAD_PREFIX,
        interval,
    )
    while True:
        try:
            n = scan_once()
            if n:
                logger.info("Passage terminé : %d fichier(s) déclenché(s)", n)
        except Exception:
            logger.exception("Erreur pendant le passage de scrutation")
        if once:
            break
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Déclencheur Garage → pipeline")
    parser.add_argument("--once", action="store_true", help="un seul passage puis sortie")
    parser.add_argument("--interval", type=float, default=5.0, help="intervalle en secondes")
    args = parser.parse_args()
    run(interval=args.interval, once=args.once)


if __name__ == "__main__":
    main()
