"""Création de la table api_keys (authentification des web services).

Revision ID: 0015_api_keys
Revises: 0014_mail_configs
Create Date: 2026-07-01

Une api_key identifie un web service appelant les endpoints API d'Alambic. La
valeur en clair n'est jamais stockée : seul son hash SHA-256 (key_hash) est
conservé, avec un préfixe non secret (key_prefix) pour l'affichage. La portée
est définie par account_id (compte cible) ou is_admin (tous comptes).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_api_keys"
down_revision = "0014_mail_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("apikey_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # Colonnes d'audit/timestamp (mixins).
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])
    op.create_index("ix_api_keys_account_id", "api_keys", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_account_id", table_name="api_keys")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
