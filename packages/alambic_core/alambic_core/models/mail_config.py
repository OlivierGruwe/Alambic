"""alambic_core.models.mail_config — configuration d'ingestion par mail (IMAP).

Une MailConfig décrit une boîte mail à interroger périodiquement : ses mails
non lus sont récupérés, transformés en fichiers, et injectés dans le pipeline
Alambic comme un dépôt normal (rattachés à la Config/compte cible).

Principes :
- vraies colonnes SQL (pas de blob JSON) pour rester interrogeable ;
- le mot de passe IMAP est stocké CHIFFRÉ (EncryptedString), comme la clé EdenAI ;
- content_mode est une colonne unique ('all'/'body'/'attachments') plutôt qu'un
  couple de booléens qui autoriserait un état contradictoire.

Le protocole est volontairement limité à IMAP pour ce premier lot (POP3 écarté :
il ne sait pas marquer « lu » et est incompatible avec un polling robuste ;
Azure/Graph prévu pour un lot ultérieur).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str
from ..db.types import EncryptedString

# Modes de traitement du contenu d'un mail.
CONTENT_MODES = ("all", "body", "attachments")
# Actions post-traitement applicables à un mail ingéré.
POST_ACTIONS = ("seen", "move", "delete", "none")


class MailConfig(Base, TimestampMixin, AuditMixin):
    """Boîte mail IMAP interrogée périodiquement pour alimenter le pipeline."""

    __tablename__ = "mail_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    # Identité / rattachement.
    mailconfig_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email_address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    # Config Alambic + compte vers lesquels router les mails ingérés.
    config_id: Mapped[str | None] = mapped_column(
        ForeignKey("configs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Désactivable sans suppression (le polling l'ignore si False).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Connexion IMAP ───────────────────────────────────────────────────────
    imap_server: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    # Mot de passe IMAP chiffré au repos (déchiffré à la lecture par EncryptedString).
    imap_password_enc: Mapped[str] = mapped_column(
        EncryptedString(2048), nullable=False, default=""
    )
    imap_inbox: Mapped[str] = mapped_column(String(255), nullable=False, default="INBOX")
    # Critère de recherche IMAP (par défaut : non lus).
    imap_search_criteria: Mapped[str] = mapped_column(
        String(255), nullable=False, default="(UNSEEN)"
    )
    # Alias optionnel : ne récupérer que les mails adressés à cette adresse (TO).
    imap_alias: Mapped[str] = mapped_column(String(320), nullable=False, default="")

    # ── Traitement du contenu ────────────────────────────────────────────────
    # 'all' (corps + PJ) | 'body' (corps seul) | 'attachments' (PJ seules).
    content_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="all")
    # Extensions de PJ autorisées (".pdf,.docx"), vide = toutes. Ignoré en 'body'.
    filter_attachment_extensions: Mapped[str] = mapped_column(
        String(1024), nullable=False, default=""
    )
    # Whitelist d'expéditeurs (patterns wildcards), vide = tous acceptés.
    sender_whitelist: Mapped[str] = mapped_column(String(4096), nullable=False, default="")

    # ── Action post-traitement ───────────────────────────────────────────────
    # 'seen' (marquer lu) | 'move' (déplacer) | 'delete' | 'none'.
    after_process_action: Mapped[str] = mapped_column(String(20), nullable=False, default="seen")
    # Dossier destination si after_process_action == 'move'.
    after_process_folder: Mapped[str] = mapped_column(
        String(255), nullable=False, default="ARCHIVE"
    )

    def __repr__(self) -> str:
        return f"<MailConfig {self.id} {self.email_address} imap>"
