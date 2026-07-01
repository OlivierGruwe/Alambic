"""Découplage des coûts : SET NULL au lieu de CASCADE sur costs.

Revision ID: 0017_cost_preserve
Revises: 0016_account_contact
Create Date: 2026-07-01

Jusqu'ici, supprimer une transaction (ou un document) supprimait en cascade ses
lignes de coût, ce qui effaçait l'historique statistique (projection de coûts,
dépenses par période, autonomie EdenAI). On passe les FK costs.transaction_id et
costs.document_id de CASCADE à SET NULL : la suppression met la référence à NULL
au lieu de supprimer le coût. Le coût conserve account_id, montant, date et
process — les statistiques restent complètes.

Les noms de contraintes suivent la convention PostgreSQL par défaut
(<table>_<colonne>_fkey), les FK ayant été créées sans nom explicite en 0001.
"""

from __future__ import annotations

from alembic import op

revision = "0017_cost_preserve"
down_revision = "0016_account_contact"
branch_labels = None
depends_on = None

_TX_FK = "costs_transaction_id_fkey"
_DOC_FK = "costs_document_id_fkey"


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite ne gère pas ALTER de contraintes : on ignore (les tests SQLite
    # recréent le schéma depuis les modèles, déjà en SET NULL).
    if bind.dialect.name == "sqlite":
        return

    # transaction_id : CASCADE → SET NULL.
    op.drop_constraint(_TX_FK, "costs", type_="foreignkey")
    op.create_foreign_key(
        _TX_FK, "costs", "transactions", ["transaction_id"], ["id"], ondelete="SET NULL"
    )
    # document_id : CASCADE → SET NULL.
    op.drop_constraint(_DOC_FK, "costs", type_="foreignkey")
    op.create_foreign_key(
        _DOC_FK, "costs", "documents", ["document_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    op.drop_constraint(_TX_FK, "costs", type_="foreignkey")
    op.create_foreign_key(
        _TX_FK, "costs", "transactions", ["transaction_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint(_DOC_FK, "costs", type_="foreignkey")
    op.create_foreign_key(
        _DOC_FK, "costs", "documents", ["document_id"], ["id"], ondelete="CASCADE"
    )
