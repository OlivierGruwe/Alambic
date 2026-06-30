"""alambic_core.models.transaction — modèle Transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str


class Transaction(Base, TimestampMixin, AuditMixin):
    """Transaction (un lot entrant). Reprend fcl_transaction.FclTransaction.

    AVEC versioning optimiste : plusieurs documents la mettent à jour en
    parallèle (cf. DispatchProcessing dans l'ASL), et ton code avait du
    try_set_status dessus. C'est le cas de concurrence à protéger.
    """

    __tablename__ = "transactions"

    # Index unique PARTIEL sur transaction_key : garantit qu'une même clé
    # déterministe (hash fichier) ne peut désigner qu'une seule transaction —
    # c'est le filet dur sous l'idempotence applicative de start_ingestion, qui
    # ferme le trou de concurrence (deux dépôts simultanés du même fichier).
    # Partiel (WHERE transaction_key != '') car les transactions REJECTED ou
    # sans clé ont transaction_key = "" et ne doivent pas entrer en collision.
    __table_args__ = (
        Index(
            "uq_transactions_transaction_key",
            "transaction_key",
            unique=True,
            postgresql_where=text("transaction_key <> ''"),
            sqlite_where=text("transaction_key <> ''"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    # Versioning optimiste natif (remplace try_set_status)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    transaction_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="", index=True)
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    process: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    # Horodatage de la dernière étape franchie (état courant rapide ; le détail
    # par étape vit dans transaction_steps).
    process_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Date de l'export réussi. Base du calcul de rétention : la transaction
    # devient purgeable à exported_at + retention_days (défini par sa config).
    exported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Compteur de documents écartés (DISCARDED), pour faire remonter l'info au
    # niveau transaction sans recompter à chaque fois.
    nb_discarded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nb_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Déblocage manuel de la complétude : un opérateur a forcé l'export d'un
    # dossier incomplet en connaissance de cause.
    completeness_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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
    # Lecture seule : sert à lire la rétention (general.retention_days) de la
    # config. viewonly pour ne pas interférer avec la gestion de config_id.
    config: Mapped["Config | None"] = relationship(
        primaryjoin="Transaction.config_id == Config.id",
        foreign_keys="Transaction.config_id",
        viewonly=True,
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    steps: Mapped[list["TransactionStep"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="TransactionStep.started_at",
    )

    __mapper_args__ = {"version_id_col": version}

    def __repr__(self) -> str:
        return f"<Transaction {self.id} status={self.status} v={self.version}>"
