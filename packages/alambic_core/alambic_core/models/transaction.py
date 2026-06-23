"""alambic_core.models.transaction — modèle Transaction."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, AuditMixin, uuid_str


class Transaction(Base, TimestampMixin, AuditMixin):
    """Transaction (un lot entrant). Reprend fcl_transaction.FclTransaction.

    AVEC versioning optimiste : plusieurs documents la mettent à jour en
    parallèle (cf. DispatchProcessing dans l'ASL), et ton code avait du
    try_set_status dessus. C'est le cas de concurrence à protéger.
    """

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    # Versioning optimiste natif (remplace try_set_status)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    transaction_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="", index=True)
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    process: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    nb_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    backup_bucket: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    backup_key: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    # FK
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    config_id: Mapped[str | None] = mapped_column(
        ForeignKey("configs.id", ondelete="SET NULL"), nullable=True
    )

    # Relations
    account: Mapped["Account"] = relationship(back_populates="transactions")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}

    def __repr__(self) -> str:
        return f"<Transaction {self.id} status={self.status} v={self.version}>"
