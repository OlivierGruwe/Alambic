"""migration initiale — schéma complet Alambic

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-23

Migration initiale unique (repart « from scratch ») : crée tout le schéma
Alambic en une fois — les 8 tables métier + la table users (auth souveraine) +
l'index unique partiel sur transaction_key (idempotence de l'ingestion).

Remplace l'ancien historique (dbec919741c2). Pour un déploiement neuf :
    alembic upgrade head      # crée le schéma
    make bootstrap            # crée le 1er super-admin
    make seed                 # charge accounts + doctypes de référence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.TIMESTAMP()
_now = sa.text("now()")


def upgrade() -> None:
    # ── accounts ─────────────────────────────────────────────────────────────
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("address", postgresql.JSONB(), nullable=False),
        sa.Column("zip", sa.String(20), nullable=False),
        sa.Column("town", sa.String(255), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("edenai_secret_key", sa.String(2048), nullable=False),
        sa.Column("keys", sa.String(8192), nullable=False),
        sa.Column("enrich_allowed_domains", sa.String(2048), nullable=False),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )

    # ── configs ──────────────────────────────────────────────────────────────
    op.create_table(
        "configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("config_name", sa.String(255), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("doctype_id", sa.String(36), nullable=False),
        sa.Column("need_validation", sa.Boolean(), nullable=False),
        sa.Column("multi_doc_detect", sa.Boolean(), nullable=False),
        sa.Column("general", postgresql.JSONB(), nullable=False),
        sa.Column("edenai_settings", postgresql.JSONB(), nullable=False),
        sa.Column("ws", postgresql.JSONB(), nullable=False),
        sa.Column("ftp_in_enc", sa.String(8192), nullable=False),
        sa.Column("ftp_out_enc", sa.String(8192), nullable=False),
        sa.Column("aws_in_enc", sa.String(8192), nullable=False),
        sa.Column("aws_out_enc", sa.String(8192), nullable=False),
        sa.Column("flower_enc", sa.String(8192), nullable=False),
        sa.Column("edenai_secret_enc", sa.String(4096), nullable=False),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )

    # ── doctypes ─────────────────────────────────────────────────────────────
    op.create_table(
        "doctypes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doctype_name", sa.String(255), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("json_content", sa.Text(), nullable=False),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )

    # ── users (auth souveraine) ──────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("auth_provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_account_id", "users", ["account_id"])
    op.create_index("ix_users_external_id", "users", ["external_id"])

    # ── transactions ─────────────────────────────────────────────────────────
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("transaction_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(40), nullable=False),
        sa.Column("process", sa.String(40), nullable=False),
        sa.Column("nb_docs", sa.Integer(), nullable=False),
        sa.Column("backup_bucket", sa.String(255), nullable=False),
        sa.Column("backup_key", sa.String(1024), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("config_id", sa.String(36), sa.ForeignKey("configs.id", ondelete="SET NULL")),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )
    # Index unique PARTIEL : idempotence de l'ingestion (clé non vide unique).
    op.create_index(
        "uq_transactions_transaction_key",
        "transactions",
        ["transaction_key"],
        unique=True,
        postgresql_where=sa.text("transaction_key <> ''"),
    )

    # ── documents ────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("process", sa.String(40), nullable=False),
        sa.Column("process_state", sa.String(20), nullable=False),
        sa.Column("bucket_name", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("doctype", sa.String(255), nullable=False),
        sa.Column("doctype_desc", sa.Text(), nullable=False),
        sa.Column("extraction_summary", postgresql.JSONB(), nullable=False),
        sa.Column(
            "transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doctype_id", sa.String(36), sa.ForeignKey("doctypes.id", ondelete="SET NULL")),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
    )

    # ── document_indexes ─────────────────────────────────────────────────────
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
        sa.Column("index_value", sa.Text(), nullable=False),
        sa.Column("index_score", sa.String(40), nullable=False),
        sa.Column("index_desc", sa.Text(), nullable=False),
    )

    # ── costs ────────────────────────────────────────────────────────────────
    op.create_table(
        "costs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("amount", sa.Numeric(12, 6), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column(
            "transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
        ),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("process", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("details", sa.String(1024), nullable=False),
        sa.Column("month", sa.String(2), nullable=False),
        sa.Column("year", sa.String(4), nullable=False),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
    )

    # ── messages ─────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
        ),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", _TS, server_default=_now, nullable=False),
        sa.Column("updated_at", _TS, server_default=_now, nullable=False),
        sa.CheckConstraint(
            "(transaction_id IS NOT NULL) <> (document_id IS NOT NULL)",
            name="ck_message_one_parent",
        ),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("costs")
    op.drop_table("document_indexes")
    op.drop_table("documents")
    op.drop_index("uq_transactions_transaction_key", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_index("ix_users_account_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("doctypes")
    op.drop_table("configs")
    op.drop_table("accounts")
