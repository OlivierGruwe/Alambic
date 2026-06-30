"""alambic_core.mail.imap_client — client IMAP pour l'ingestion par mail.

Porté depuis flowerscan_lib.fml_imap_config, adapté à Alambic : reçoit ses
paramètres explicitement (pas de couplage au modèle), expose la récupération des
mails non lus et les actions post-traitement (marquer lu / déplacer / supprimer).

Robustesse : timeout réseau systématique, limite du nombre de mails par passage
(évite de charger une grosse boîte en mémoire), fermeture propre de la connexion.
"""

from __future__ import annotations

import imaplib
import logging
from dataclasses import dataclass

logger = logging.getLogger("alambic.mail.imap")

IMAP_TIMEOUT = 10  # secondes
MAX_MAILS = 200  # plafond de messages récupérés par passage


@dataclass
class ImapParams:
    """Paramètres de connexion IMAP (résolus depuis une MailConfig)."""

    server: str
    port: int
    email: str
    password: str
    inbox: str = "INBOX"
    search_criteria: str = "(UNSEEN)"
    alias: str = ""


class ImapClient:
    """Client IMAP minimal : connexion paresseuse, fetch borné, post-actions."""

    def __init__(self, params: ImapParams):
        self.params = params
        self._conn: imaplib.IMAP4_SSL | None = None

    @property
    def connection(self) -> imaplib.IMAP4_SSL:
        """Connexion IMAP SSL (ouverte paresseusement, avec timeout)."""
        if self._conn is None:
            self._conn = imaplib.IMAP4_SSL(
                self.params.server, self.params.port, timeout=IMAP_TIMEOUT
            )
            self._conn.login(self.params.email, self.params.password)
        return self._conn

    def close(self) -> None:
        """Ferme proprement la connexion (idempotent, jamais bloquant)."""
        if self._conn is None:
            return
        from contextlib import suppress

        try:
            with suppress(Exception):  # close() échoue si pas de mailbox sélectionnée
                self._conn.close()
            self._conn.logout()
        except Exception:  # noqa: BLE001 — best-effort
            pass
        finally:
            self._conn = None

    def __enter__(self) -> ImapClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def count_unseen(self) -> int:
        """Nombre de messages correspondant au critère (sans les récupérer)."""
        self.connection.select(self.params.inbox, readonly=True)
        result, data = self._search()
        if result != "OK" or not data or not data[0]:
            return 0
        return len(data[0].split())

    def _search(self):
        """Recherche IMAP selon le critère (+ filtre alias TO si défini)."""
        if self.params.alias:
            return self.connection.search(
                None, self.params.search_criteria, f'(TO "{self.params.alias}")'
            )
        return self.connection.search(None, self.params.search_criteria)

    def fetch_mails(self) -> list[dict]:
        """Récupère les mails (jusqu'à MAX_MAILS). Renvoie [{id, content}].

        `content` est le message brut (RFC822, bytes) ; le parsing (corps, PJ,
        expéditeur) est fait par la couche appelante.
        """
        self.connection.select(self.params.inbox)
        result, data = self._search()
        if result != "OK" or not data or not data[0]:
            return []

        email_ids = data[0].split()[:MAX_MAILS]
        mails = []
        for email_id in email_ids:
            try:
                _res, msg_data = self.connection.fetch(email_id, "(RFC822)")
                for part in msg_data:
                    if isinstance(part, tuple):
                        mails.append(
                            {"id": email_id.decode(), "content": part[1]}
                        )
            except Exception as exc:  # noqa: BLE001 — un mail KO ne bloque pas les autres
                logger.warning(
                    "Échec récupération mail %s : %s",
                    email_id.decode() if hasattr(email_id, "decode") else email_id,
                    exc,
                )
        return mails

    def apply_post_action(self, email_id: str, action: str, folder: str = "ARCHIVE") -> None:
        """Applique l'action post-traitement sur un message.

        'seen'   → marque lu (reste dans la boîte) ;
        'move'   → copie vers `folder` puis supprime de la boîte courante ;
        'delete' → supprime définitivement (Deleted + expunge) ;
        'none'   → ne fait rien.
        """
        action = (action or "seen").lower()
        if action == "none":
            return

        if action == "delete":
            self.connection.store(email_id, "+FLAGS", "\\Deleted")
            self.connection.expunge()
            return

        if action == "move":
            from contextlib import suppress

            with suppress(Exception):  # le dossier existe déjà
                self.connection.create(folder)
            self.connection.copy(email_id, folder)
            self.connection.store(email_id, "+FLAGS", "\\Deleted")
            self.connection.expunge()
            return

        # 'seen' (défaut).
        self.connection.store(email_id, "+FLAGS", "\\Seen")


def imap_params_from_config(mail_config) -> ImapParams:
    """Construit les paramètres IMAP depuis une MailConfig (secret déchiffré)."""
    return ImapParams(
        server=mail_config.imap_server,
        port=mail_config.imap_port,
        email=mail_config.email_address,
        password=mail_config.imap_password_enc,  # déchiffré par EncryptedString
        inbox=mail_config.imap_inbox,
        search_criteria=mail_config.imap_search_criteria or "(UNSEEN)",
        alias=mail_config.imap_alias or "",
    )
