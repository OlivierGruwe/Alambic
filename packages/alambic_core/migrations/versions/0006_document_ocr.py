"""Ajout des champs OCR sur documents (ocr_markdown, ocr_lines).

Revision ID: 0006_document_ocr
Revises: 0005_document_barcodes
Create Date: 2026-06-25

ocr_markdown : texte structuré (par page) pour la classification.
ocr_lines    : lignes positionnées (texte b64 + position %) pour l'extraction
               de champs et le découpage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_document_ocr"
down_revision = "0005_document_barcodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("ocr_markdown", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "documents",
        sa.Column("ocr_lines", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("documents", "ocr_lines")
    op.drop_column("documents", "ocr_markdown")
