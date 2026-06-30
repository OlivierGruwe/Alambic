"""Retrait de completeness_check (complétude désormais implicite).

Revision ID: 0009_drop_completeness_check
Revises: 0008_config_active
Create Date: 2026-06-29

Le contrôle de complétude n'est plus piloté par un drapeau : il s'active
automatiquement dès qu'au moins un doctype attendu (expected_doctypes) est marqué
obligatoire. La colonne completeness_check devient morte → on la retire.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_drop_completeness_check"
down_revision = "0008_config_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("configs", "completeness_check")


def downgrade() -> None:
    op.add_column(
        "configs",
        sa.Column(
            "completeness_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
