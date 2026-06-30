"""Ajout de config_fields (champs propagés) sur configs.

Revision ID: 0010_config_fields
Revises: 0009_drop_completeness_check
Create Date: 2026-06-30

config_fields : liste de champs déclarés au niveau config dont la valeur est
résolue par transaction (contexte source ou token calculé) et propagée comme
index metadata sur chaque document. Défaut [] : aucun champ propagé.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_config_fields"
down_revision = "0009_drop_completeness_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configs",
        sa.Column(
            "config_fields",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("configs", "config_fields")
