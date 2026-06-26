"""Ajout de barcodes sur documents (readCAB).

Revision ID: 0005_document_barcodes
Revises: 0004_exported_at
Create Date: 2026-06-25

Stocke les codes-barres lus par readCAB (liste de {value, page, format,
position}) directement sur le document : lecture sans I/O réseau et disponible
pour le découpage. Défaut : liste vide.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_document_barcodes"
down_revision = "0004_exported_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "barcodes",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "barcodes")
