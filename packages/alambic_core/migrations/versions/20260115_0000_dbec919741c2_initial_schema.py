"""initial schema — 8 tables coeur

Revision ID: dbec919741c2
Revises:
Create Date: 2026-01-15

Crée le schéma initial d'Alambic : accounts, configs, doctypes, transactions,
documents, document_indexes, messages, costs.

Optimisations vs DynamoDB :
  - Les ex-tables *_indexes deviennent de vrais index SQL.
  - JSONB natif pour le schemaless (extraction_summary, blocs de config).
  - Index GIN sur les colonnes JSONB interrogeables.
  - Secrets stockés chiffrés (colonnes *_enc / edenai_secret_key).
  - version (verrou optimiste) uniquement sur documents et transactions.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "dbec919741c2"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── accounts ─────────────────────────────────────────────────────────────
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("address", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("zip", sa.String(20), nullable=False, server_default=""),
        sa.Column("town", sa.String(255), nullable=False, server_default=""),
        sa.Column("country", sa.String(100), nullable=False, server_default=""),
        sa.Column("edenai_secret_key", sa.String(2048), nullable=False, server_default=""),
        sa.Column("keys", sa.String(8192), nullable=False, server_default=""),
        sa.Column("enrich_allowed_domains", sa.String(2048), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )

    # ── configs ──────────────────────────────────────────────────────────────
    op.create_table(
        "configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("config_name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("doctype_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("need_validation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("multi_doc_detect", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("general", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("edenai_settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ws", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ftp_in_enc", sa.String(8192), nullable=False, server_default=""),
        sa.Column("ftp_out_enc", sa.String(8192), nullable=False, server_default=""),
        sa.Column("aws_in_enc", sa.String(8192), nullable=False, server_default=""),
        sa.Column("aws_out_enc", sa.String(8192), nullable=False, server_default=""),
        sa.Column("flower_enc", sa.String(8192), nullable=False, server_default=""),
        sa.Column("edenai_secret_enc", sa.String(4096), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )
    op.create_index("ix_configs_account_id", "configs", ["account_id"])

    # ── doctypes ─────────────────────────────────────────────────────────────
    op.create_table(
        "doctypes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doctype_name", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("json_content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )

    # ── transactions (avec version) ──────────────────────────────────────────
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("transaction_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(40), nullable=False, server_default=""),
        sa.Column("origin", sa.String(40), nullable=False, server_default=""),
        sa.Column("process", sa.String(40), nullable=False, server_default=""),
        sa.Column("nb_docs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("backup_bucket", sa.String(255), nullable=False, server_default=""),
        sa.Column("backup_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "config_id",
            sa.String(36),
            sa.ForeignKey("configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_status", "transactions", ["status"])

    # ── documents (avec version) ─────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(40), nullable=False, server_default=""),
        sa.Column("process", sa.String(40), nullable=False, server_default="NEWDOC"),
        sa.Column("process_state", sa.String(20), nullable=False, server_default="STARTED"),
        sa.Column("bucket_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("object_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column("doctype", sa.String(255), nullable=False, server_default="unknown"),
        sa.Column("doctype_desc", sa.Text(), nullable=False, server_default=""),
        sa.Column("extraction_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doctype_id",
            sa.String(36),
            sa.ForeignKey("doctypes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_transaction_id", "documents", ["transaction_id"])
    # Index GIN sur le JSONB interrogeable (recherche dans extraction_summary)
    op.create_index(
        "ix_documents_extraction_summary_gin",
        "documents",
        ["extraction_summary"],
        postgresql_using="gin",
    )

    # ── document_indexes (table dédiée, ex-DynamoDB documents_indexes) ───────
    op.create_table(
        "document_indexes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index_type", sa.String(20), nullable=False),
        sa.Column("index_name", sa.String(255), nullable=False),
        sa.Column("index_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("index_score", sa.String(40), nullable=False, server_default=""),
        sa.Column("index_desc", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_docidx_parent_type_name",
        "document_indexes",
        ["document_id", "index_type", "index_name"],
    )

    # ── messages (parent polymorphe : tx XOR doc) ────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("level", sa.String(20), nullable=False, server_default="INFO"),
        sa.Column("source", sa.String(100), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "(transaction_id IS NOT NULL) <> (document_id IS NOT NULL)",
            name="ck_message_one_parent",
        ),
    )
    op.create_index("ix_messages_transaction_id", "messages", ["transaction_id"])
    op.create_index("ix_messages_document_id", "messages", ["document_id"])

    # ── costs ────────────────────────────────────────────────────────────────
    op.create_table(
        "costs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("amount", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("process", sa.String(40), nullable=False, server_default=""),
        sa.Column("provider", sa.String(100), nullable=False, server_default=""),
        sa.Column("model", sa.String(255), nullable=False, server_default=""),
        sa.Column("details", sa.String(1024), nullable=False, server_default=""),
        sa.Column("month", sa.String(2), nullable=False, server_default=""),
        sa.Column("year", sa.String(4), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_costs_transaction_id", "costs", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("costs")
    op.drop_table("messages")
    op.drop_table("document_indexes")
    op.drop_table("documents")
    op.drop_table("transactions")
    op.drop_table("doctypes")
    op.drop_table("configs")
    op.drop_table("accounts")
