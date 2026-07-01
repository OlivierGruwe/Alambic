"""Registre d'import FTP/S3 (dédoublonnage).

Revision ID: 0018_ingest_registry
Revises: 0017_cost_preserve
Create Date: 2026-07-01

Crée la table ingest_registry : trace les fichiers déjà importés depuis une
source d'entrée (FTP ou S3) pour ne jamais les ré-importer. Unicité sur
(config_id, source_key).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_ingest_registry"
down_revision = "0017_cost_preserve"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_now = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "ingest_registry",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "config_id",
            sa.String(36),
            sa.ForeignKey("configs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(8), nullable=False, server_default=""),
        sa.Column("source_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column("last_seen_at", _TS, server_default=_now, nullable=False),
        sa.Column("upload_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column("transaction_id", sa.String(36), nullable=True),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.UniqueConstraint("config_id", "source_key", name="uq_ingest_config_source"),
    )
    op.create_index("ix_ingest_registry_config_id", "ingest_registry", ["config_id"])
    op.create_index("ix_ingest_registry_last_seen_at", "ingest_registry", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_ingest_registry_last_seen_at", table_name="ingest_registry")
    op.drop_index("ix_ingest_registry_config_id", table_name="ingest_registry")
    op.drop_table("ingest_registry")
