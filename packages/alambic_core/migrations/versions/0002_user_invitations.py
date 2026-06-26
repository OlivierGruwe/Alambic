"""ajout des champs d'invitation utilisateur

Revision ID: 0002_user_invitations
Revises: 0001_initial
Create Date: 2026-06-24

Ajoute invite_token + invite_expires_at à la table users, pour le flux
d'invitation (l'utilisateur définit son mot de passe via un jeton à usage unique).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_invitations"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("invite_token", sa.String(64), nullable=True))
    op.add_column(
        "users", sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_users_invite_token", "users", ["invite_token"])


def downgrade() -> None:
    op.drop_index("ix_users_invite_token", table_name="users")
    op.drop_column("users", "invite_expires_at")
    op.drop_column("users", "invite_token")
