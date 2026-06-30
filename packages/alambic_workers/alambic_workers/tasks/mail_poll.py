"""alambic_workers.tasks.mail_poll — relève périodique des boîtes mail (IMAP).

Balayée par Celery Beat. Pour chaque MailConfig active :
  1. se connecte en IMAP, récupère les mails non lus (bornés) ;
  2. filtre l'expéditeur (whitelist) — un mail rejeté n'est pas ingéré ;
  3. dépose le mail brut (.eml) dans Garage et déclenche l'ingestion, en
     transmettant la politique de contenu (content_mode + filtre PJ) à
     l'extraction : l'EmlProcessor crée alors une transaction par mail avec un
     document par pièce jointe (+ un document pour le corps selon le mode) ;
  4. applique l'action post-traitement au mail (marquer lu / déplacer / supprimer).

Souveraineté : IMAP uniquement (serveur mail au choix). Azure/Graph viendra dans
un lot ultérieur.
"""

from __future__ import annotations

import logging
import os
import tempfile

from alambic_core import storage
from alambic_core.db.session import session_scope
from alambic_core.mail import ImapClient, imap_params_from_config
from alambic_core.mail.parsing import sender_filter_from_config, sender_of
from alambic_core.models import MailConfig

from alambic_workers.celery_app import app
from alambic_workers.tasks.start_ingestion import start_ingestion

logger = logging.getLogger(__name__)

INPUT_BUCKET = os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input")


def _ingest_mail(mail_config, mail: dict, work_dir: str) -> bool:
    """Dépose un mail (.eml) et déclenche l'ingestion. Renvoie True si lancé."""
    raw = mail["content"]
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")

    # Nom de fichier .eml unique (par id de message), déposé sous la clé d'upload
    # qui rattache le flux à la Config/compte cible.
    filename = f"mail_{mail['id']}.eml"
    object_key = storage.build_upload_key(
        mail_config.account_id or "",
        mail_config.config_id or "",
        filename,
        origin="MAIL",
    )

    local_path = os.path.join(work_dir, filename)
    with open(local_path, "wb") as fh:
        fh.write(raw)
    storage.put_object(INPUT_BUCKET, object_key, local_path)

    # La politique de contenu mail descend jusqu'à l'EmlProcessor via metadata.
    metadata = {
        "mail_policy": {
            "content_mode": mail_config.content_mode or "all",
            "filter_attachment_extensions": mail_config.filter_attachment_extensions or "",
        }
    }
    result = start_ingestion(
        bucket=INPUT_BUCKET,
        object_key=object_key,
        local_path=local_path,
        original_filename=filename,
        author=mail_config.email_address,
        metadata=metadata,
    )
    return bool(result)


def _poll_one(mail_config) -> dict:
    """Relève une boîte mail. Renvoie un compte-rendu {fetched, ingested, skipped}."""
    sender_filter = sender_filter_from_config(mail_config)
    action = mail_config.after_process_action or "seen"
    folder = mail_config.after_process_folder or "ARCHIVE"

    fetched = ingested = skipped = 0
    client = ImapClient(imap_params_from_config(mail_config))
    try:
        mails = client.fetch_mails()
        fetched = len(mails)
        with tempfile.TemporaryDirectory(prefix="alambic_mail_") as work_dir:
            for mail in mails:
                # Filtre expéditeur : un mail hors whitelist est ignoré (et
                # marqué traité pour ne pas le re-relever indéfiniment).
                if not sender_filter.allowed(sender_of(mail["content"])):
                    skipped += 1
                    client.apply_post_action(mail["id"], action, folder)
                    continue
                try:
                    if _ingest_mail(mail_config, mail, work_dir):
                        ingested += 1
                    # Mail pris en charge → action post-traitement (lu/déplacé/supprimé).
                    client.apply_post_action(mail["id"], action, folder)
                except Exception:
                    logger.exception(
                        "Échec ingestion du mail %s (config %s)", mail["id"], mail_config.id
                    )
    finally:
        client.close()

    return {"fetched": fetched, "ingested": ingested, "skipped": skipped}


@app.task(name="alambic_workers.mail.poll", bind=True)
def poll_mailboxes(self) -> dict:
    """Relève toutes les boîtes mail actives (tâche Celery Beat).

    Renvoie un récapitulatif agrégé. Une boîte en échec (serveur injoignable,
    identifiants invalides) ne bloque pas les autres.
    """
    summary = {"configs": 0, "fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}

    with session_scope() as s:
        configs = s.query(MailConfig).filter_by(is_active=True).all()
        # Détacher les valeurs nécessaires (la session se ferme avant le réseau).
        configs = list(configs)
        for c in configs:
            _ = (c.imap_server, c.imap_password_enc, c.email_address)  # force le chargement

    for mail_config in configs:
        summary["configs"] += 1
        try:
            res = _poll_one(mail_config)
            summary["fetched"] += res["fetched"]
            summary["ingested"] += res["ingested"]
            summary["skipped"] += res["skipped"]
        except Exception:
            summary["errors"] += 1
            logger.exception("Relève de la boîte mail %s en échec", mail_config.id)

    logger.info("Relève mail : %s", summary)
    return summary
