"""Ajout des champs de contact (responsable) à la table accounts.

Revision ID: 0016_account_contact
Revises: 0015_api_keys
Create Date: 2026-07-01

Ajoute 4 colonnes de contact au compte : nom du responsable, fonction/rôle,
email et téléphone. Vraies colonnes SQL (interrogeables), avec valeur par défaut
vide pour ne pas casser les comptes existants.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_account_contact"
down_revision = "0015_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "accounts",
        sa.Column("contact_role", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "accounts",
        sa.Column("contact_email", sa.String(length=320), nullable=False, server_default=""),
    )
    op.add_column(
        "accounts",
        sa.Column("contact_phone", sa.String(length=50), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("accounts", "contact_phone")
    op.drop_column("accounts", "contact_email")
    op.drop_column("accounts", "contact_role")
    op.drop_column("accounts", "contact_name")
