"""alambic_core.models.cost — modèle Cost (coûts EdenAI par document/transaction)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_str


class Cost(Base, TimestampMixin):
    """Coût d'un appel IA. Reprend fcl_cost.FclCost.

    Liaison transaction/document pour agréger le coût par transaction.
    amount passe de str (DynamoDB) à Numeric (vrai type décimal en SQL).
    """

    __tablename__ = "costs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    amount: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    # SET NULL (et non CASCADE) : supprimer une transaction ou un document NE
    # supprime PAS ses coûts — on les découple (référence → NULL) pour préserver
    # l'historique statistique (projection, dépenses, autonomie). Le coût garde
    # son account_id, son montant, sa date et son process.
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    process: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # source : sous-chemin de l'étape. Pour la classification, identifie le nœud
    # de la cascade ("lexical_v..."/"embedding_v..."/"llm_v...") → permet de
    # mesurer la part gratuite (local) vs payante (LLM) sans parser `details`.
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    details: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # month/year conservés pour les agrégations existantes
    month: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    year: Mapped[str] = mapped_column(String(4), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<Cost {self.id} {self.amount} {self.provider}>"
