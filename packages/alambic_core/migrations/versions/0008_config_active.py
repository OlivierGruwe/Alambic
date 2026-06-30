"""Ajout du drapeau is_active sur configs.

Revision ID: 0008_config_active
Revises: 0007_config_completeness
Create Date: 2026-06-29

is_active : une config inactive n'accepte plus de nouvelles transactions
    (l'ingestion la refuse) et apparaît grisée dans les listes. Les transactions
    passées et les fichiers existants ne sont pas affectés. Défaut true : les
    configs existantes restent actives.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_config_active"
down_revision = "0007_config_completeness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configs",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("configs", "is_active")
