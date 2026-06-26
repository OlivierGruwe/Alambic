"""journal des étapes + filiation et écartement des documents

Revision ID: 0003_steps_filiation
Revises: 0002_user_invitations
Create Date: 2026-06-24

Pose la fondation du pipeline de traitement :
- transactions : process_time (horodatage de la dernière étape) + nb_discarded
  (compteur de documents écartés, pour faire remonter l'info).
- documents : process_time (suivi fin, chaque document avance à son rythme),
  parent_id (filiation auto-référente : un eml extrait produit des enfants),
  discard_reason (raison d'écartement, pour les DISCARDED uniquement).
- table transaction_steps : journal d'une ligne par étape franchie (process,
  horodatages, durée, statut), pour l'historique complet de la transaction.

Les statuts DEPRECATED (remplacé par ses enfants) et DISCARDED (écarté,
inexploitable) sont des valeurs applicatives du champ documents.status — pas de
changement de schéma pour eux.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_steps_filiation"
down_revision: str | None = "0002_user_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── transactions ────────────────────────────────────────────────────────
    op.add_column(
        "transactions",
        sa.Column("process_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("nb_discarded", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── documents : suivi fin + filiation + écartement ──────────────────────
    op.add_column(
        "documents",
        sa.Column("process_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("parent_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("discard_reason", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_documents_parent_id", "documents", ["parent_id"])
    op.create_foreign_key(
        "fk_documents_parent_id",
        "documents",
        "documents",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── transaction_steps : journal des étapes ──────────────────────────────
    op.create_table(
        "transaction_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("process", sa.String(40), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="OK"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_transaction_steps_transaction_id",
        "transaction_steps",
        ["transaction_id"],
    )
    op.create_index(
        "ix_transaction_steps_document_id",
        "transaction_steps",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transaction_steps_document_id", table_name="transaction_steps"
    )
    op.drop_index(
        "ix_transaction_steps_transaction_id", table_name="transaction_steps"
    )
    op.drop_table("transaction_steps")

    op.drop_constraint("fk_documents_parent_id", "documents", type_="foreignkey")
    op.drop_index("ix_documents_parent_id", table_name="documents")
    op.drop_column("documents", "discard_reason")
    op.drop_column("documents", "parent_id")
    op.drop_column("documents", "process_time")

    op.drop_column("transactions", "nb_discarded")
    op.drop_column("transactions", "process_time")
