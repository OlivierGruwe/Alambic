"""Retrait de la colonne morte configs.doctype_id.

Revision ID: 0012_drop_config_doctype_id
Revises: 0011_consolidation_ws
Create Date: 2026-06-30

configs.doctype_id était le repli mono-doctype historique. Il est entièrement
remplacé par expected_doctypes (liste structurée [{doctype_id, required}]), seule
source de vérité pour la classification, le découpage, les codes-barres et la
complétude. Plus aucun code ne le lit : on supprime le champ mort.

NB : ceci ne concerne QUE configs.doctype_id. La FK documents.doctype_id (type
classifié d'un document) et les doctype_id dans expected_doctypes (ID légitime
d'un doctype) sont conservés.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_drop_config_doctype_id"
down_revision = "0011_consolidation_ws"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("configs", "doctype_id")


def downgrade() -> None:
    op.add_column(
        "configs",
        sa.Column(
            "doctype_id",
            sa.String(length=36),
            nullable=False,
            server_default="",
        ),
    )
