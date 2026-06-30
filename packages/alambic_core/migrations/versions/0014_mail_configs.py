"""Création de la table mail_configs (ingestion par mail IMAP).

Revision ID: 0014_mail_configs
Revises: 0013_cost_source
Create Date: 2026-06-30

Une mail_config décrit une boîte IMAP interrogée périodiquement : ses mails non
lus sont récupérés et injectés dans le pipeline (rattachés à config_id/account_id).
Le mot de passe IMAP est chiffré au repos (colonne EncryptedString → texte).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_mail_configs"
down_revision = "0013_cost_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_configs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("mailconfig_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("email_address", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("config_id", sa.String(length=36), nullable=True),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imap_server", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("imap_password_enc", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("imap_inbox", sa.String(length=255), nullable=False, server_default="INBOX"),
        sa.Column(
            "imap_search_criteria", sa.String(length=255), nullable=False, server_default="(UNSEEN)"
        ),
        sa.Column("imap_alias", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("content_mode", sa.String(length=20), nullable=False, server_default="all"),
        sa.Column(
            "filter_attachment_extensions",
            sa.String(length=1024),
            nullable=False,
            server_default="",
        ),
        sa.Column("sender_whitelist", sa.String(length=4096), nullable=False, server_default=""),
        sa.Column(
            "after_process_action", sa.String(length=20), nullable=False, server_default="seen"
        ),
        sa.Column(
            "after_process_folder", sa.String(length=255), nullable=False, server_default="ARCHIVE"
        ),
        # Colonnes d'audit/timestamp (mixins).
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["config_id"], ["configs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_mail_configs_config_id", "mail_configs", ["config_id"])
    op.create_index("ix_mail_configs_account_id", "mail_configs", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_mail_configs_account_id", table_name="mail_configs")
    op.drop_index("ix_mail_configs_config_id", table_name="mail_configs")
    op.drop_table("mail_configs")
