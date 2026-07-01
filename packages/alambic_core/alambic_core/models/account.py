"""alambic_core.models.account — modèle Account (racine de la hiérarchie)."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str
from ..db.types import EncryptedString


class Account(Base, TimestampMixin, AuditMixin):
    """Compte client. Reprend fsl_account.FslAccount.

    Secrets chiffrés (ex-__encrypted_fields__) : edenai_secret_key, keys.
    """

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Adresse (address1..5 → un seul jsonb, plus souple que 5 colonnes)
    address: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    zip: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    town: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # ── Contact (responsable du compte) ──────────────────────────────────────
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contact_role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    contact_phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # ── Secrets chiffrés au repos ────────────────────────────────────────────
    edenai_secret_key: Mapped[str] = mapped_column(
        EncryptedString(2048), nullable=False, default=""
    )
    # 'keys' = dict de clés API (chiffré). jsonb chiffré → on stocke le jsonb
    # sérialisé chiffré ; ici EncryptedString sur la sérialisation JSON.
    keys: Mapped[str] = mapped_column(EncryptedString(8192), nullable=False, default="")

    # Allowlist anti-SSRF pour les WS d'enrichissement (chaîne, fail-closed si vide)
    enrich_allowed_domains: Mapped[str] = mapped_column(String(2048), nullable=False, default="")

    # Relations
    configs: Mapped[list["Config"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")

    def __repr__(self) -> str:
        return f"<Account {self.id} {self.account_name}>"
