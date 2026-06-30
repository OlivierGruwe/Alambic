"""alambic_core.models.message — modèle Message (journal du pipeline)."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_str


class Message(Base, TimestampMixin):
    """Message attaché à une transaction OU un document.

    Reprend fcl_message_repository : level, source, text, creation_date.
    Le parent était polymorphe en DynamoDB (parent_id = tx OU doc). En
    relationnel, deux FK nullables exclusives, plus propre que (type, id) :
    on garde de vraies contraintes d'intégrité sur chaque cible.
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Un message vise exactement UN parent (tx XOR doc), jamais zéro ni deux.
    __table_args__ = (
        CheckConstraint(
            "(transaction_id IS NOT NULL) <> (document_id IS NOT NULL)",
            name="ck_message_one_parent",
        ),
    )

    def __repr__(self) -> str:
        return f"<Message {self.level} {self.source}>"
