"""Ajout de exported_at sur transactions (rétention post-export).

Revision ID: 0004_exported_at
Revises: 0003_steps_filiation
Create Date: 2026-06-25

La date d'export sert de base au calcul de rétention : une transaction devient
purgeable à exported_at + retention_days (délai défini par sa config).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_exported_at"
down_revision = "0003_steps_filiation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "exported_at")
