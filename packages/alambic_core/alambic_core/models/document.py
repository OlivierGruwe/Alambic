"""alambic_core.models.document — modèles Document et DocumentIndex."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str
from ..domain.enums import DocumentProcess, DocumentProcessState


class Document(Base, TimestampMixin, AuditMixin):
    """Document. Reprend fcl_document.FclDocument.

    AVEC versioning optimiste : ton try_set_status protégeait déjà le document
    contre les traitements concurrents (retries, workers parallèles).
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[str] = mapped_column(String(40), nullable=False, default="", index=True)
    process: Mapped[str] = mapped_column(
        String(40), nullable=False, default=DocumentProcess.NEWDOC.value
    )
    process_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentProcessState.STARTED.value
    )

    # Stockage objet (MinIO)
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    # Classification
    doctype: Mapped[str] = mapped_column(String(255), nullable=False, default="unknown")
    doctype_desc: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Résumé d'extraction : vraiment schemaless → jsonb justifié
    extraction_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # FK réelles
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctype_id: Mapped[str | None] = mapped_column(
        ForeignKey("doctypes.id", ondelete="SET NULL"), nullable=True
    )

    # Relations
    transaction: Mapped["Transaction"] = relationship(back_populates="documents")
    indexes: Mapped[list["DocumentIndex"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )

    # Index GIN sur le JSONB interrogeable (recherche par contenu dans
    # extraction_summary, ex: WHERE extraction_summary @> '{"source":"ai"}').
    # Doit rester cohérent avec la migration Alembic initiale.
    __table_args__ = (
        Index(
            "ix_documents_extraction_summary_gin",
            "extraction_summary",
            postgresql_using="gin",
        ),
    )

    __mapper_args__ = {"version_id_col": version}

    def __repr__(self) -> str:
        return f"<Document {self.id} status={self.status} process={self.process}>"


class DocumentIndex(Base):
    """Table d'index DÉDIÉE — remplace la table DynamoDB documents_indexes.

    Reprend FclIndexModel. Table relationnelle (pas jsonb) car interrogée par
    champ : index_type, index_name, index_value. Index btree composite dessus.
    """

    __tablename__ = "document_indexes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    index_type: Mapped[str] = mapped_column(String(20), nullable=False)  # metadata|extracted
    index_name: Mapped[str] = mapped_column(String(255), nullable=False)
    index_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    index_score: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    index_desc: Mapped[str] = mapped_column(Text, nullable=False, default="")

    document: Mapped["Document"] = relationship(back_populates="indexes")

    __table_args__ = (
        Index("ix_docidx_parent_type_name", "document_id", "index_type", "index_name"),
    )

    def __repr__(self) -> str:
        return f"<DocumentIndex {self.index_type}#{self.index_name}={self.index_value}>"
