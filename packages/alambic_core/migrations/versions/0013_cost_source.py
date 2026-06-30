"""Ajout de la colonne source sur costs.

Revision ID: 0013_cost_source
Revises: 0012_drop_config_doctype_id
Create Date: 2026-06-30

source : sous-chemin de l'étape qui a généré le coût. Pour la classification,
identifie le nœud de la cascade ("lexical_v...", "embedding_v...", "llm_v...").
Permet de mesurer la part GRATUITE (lexical + embedding, calcul local) vs PAYANTE
(llm, appel EdenAI) sans parser le champ texte `details`. Défaut "".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_cost_source"
down_revision = "0012_drop_config_doctype_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "costs",
        sa.Column("source", sa.String(length=100), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("costs", "source")
