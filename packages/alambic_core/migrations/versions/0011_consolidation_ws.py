"""Ajout de consolidation_ws (WS d'enrichissement) sur configs.

Revision ID: 0011_consolidation_ws
Revises: 0010_config_fields
Create Date: 2026-06-30

consolidation_ws : définitions de web services appelés après extraction pour
valider/enrichir un champ (ex. vérifier un numéro de compte et récupérer le nom
du titulaire). Défaut [] : aucun WS d'enrichissement.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_consolidation_ws"
down_revision = "0010_config_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configs",
        sa.Column(
            "consolidation_ws",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("configs", "consolidation_ws")
