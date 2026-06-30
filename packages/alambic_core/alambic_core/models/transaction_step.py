"""alambic_core.models.transaction_step — journal des étapes du pipeline.

Une ligne par étape exécutée pour une transaction (FILEEXTRACTOR, OCR_DONE…).
Donne l'historique complet (qui s'est passé, quand, combien de temps), tandis
que Transaction.process / process_time ne portent que l'état courant.

Le suivi FIN d'avancement (skip de rejouabilité) se fait au niveau Document
(chaque document avance à son rythme) ; ce journal est le récit global de la
transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, uuid_str


class TransactionStep(Base):
    """Une étape franchie par une transaction (entrée de journal)."""

    __tablename__ = "transaction_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Document concerné si l'étape est propre à un document (nullable : certaines
    # étapes sont globales à la transaction).
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    process: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OK")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Durée en millisecondes (entier). Renseignée à la fin de l'étape.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Détail libre en cas d'erreur (message court).
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    transaction: Mapped["Transaction"] = relationship(back_populates="steps")

    def __repr__(self) -> str:
        return (
            f"<TransactionStep {self.process} tx={self.transaction_id} "
            f"status={self.status} {self.duration_ms}ms>"
        )
