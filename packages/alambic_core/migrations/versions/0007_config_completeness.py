"""Ajout de la complétude de dossier sur configs (expected_doctypes, completeness_check).

Revision ID: 0007_config_completeness
Revises: 0006_document_ocr
Create Date: 2026-06-29

expected_doctypes : liste structurée des doctypes attendus pour un dossier, avec
    leur caractère obligatoire/optionnel. Format :
        [{"doctype_id": "...", "required": true}, ...]
    Source de vérité pour : la classification (restriction aux types attendus),
    la vérification de complétude, et le blocage d'export.
completeness_check : si vrai, la transaction est vérifiée complète (tous les
    doctypes obligatoires présents) avant d'autoriser l'export.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_config_completeness"
down_revision = "0006_document_ocr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configs",
        sa.Column("expected_doctypes", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "configs",
        sa.Column(
            "completeness_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Déblocage manuel : un opérateur peut forcer l'export d'un dossier incomplet.
    op.add_column(
        "transactions",
        sa.Column(
            "completeness_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "completeness_override")
    op.drop_column("configs", "completeness_check")
    op.drop_column("configs", "expected_doctypes")
