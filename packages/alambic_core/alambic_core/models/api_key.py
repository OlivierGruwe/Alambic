"""alambic_core.models.api_key — clés API pour l'authentification des web services.

Une ApiKey identifie un appelant machine (web service) qui accède aux endpoints
API d'Alambic (ex. ingestion de documents). La valeur de la clé n'est JAMAIS
stockée en clair : on en garde uniquement le HASH (SHA-256). À la création, la
valeur en clair est renvoyée une seule fois à l'utilisateur, qui doit la noter —
elle ne pourra plus être réaffichée ensuite (modèle GitHub/Stripe/AWS).

Principes :
- vraies colonnes SQL (pas de blob JSON) pour rester interrogeable ;
- valeur hashée (key_hash) + préfixe non secret (key_prefix) pour l'affichage ;
- portée par compte : account_id = compte cible, ou is_admin pour « tous comptes » ;
- expiration par date (expires_at), calculée depuis une validité en jours.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str


class ApiKey(Base, TimestampMixin, AuditMixin):
    """Clé API identifiant un web service appelant les endpoints Alambic."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    # Nom lisible (identifie la clé côté administration).
    apikey_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Hash SHA-256 (hex, 64 chars) de la valeur en clair. La valeur elle-même
    # n'est jamais stockée : on ne peut que vérifier une clé présentée.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Préfixe non secret (ex. « alb_3f9c ») pour reconnaître la clé dans la liste.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    # Portée : compte cible. NULL + is_admin => clé « tous comptes ».
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Clé d'administration : accès à tous les comptes (account_id ignoré).
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Activable/désactivable sans suppression (l'auth la refuse si False).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Expiration : au-delà de cette date, la clé est refusée. NULL = sans limite.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def is_valid_now(self, now: datetime | None = None) -> bool:
        """True si la clé est active et non expirée à l'instant donné."""
        if not self.is_active:
            return False
        if self.expires_at is None:
            return True
        current = now or datetime.now(UTC)
        expires = self.expires_at
        # Robustesse tz : selon le backend, expires_at peut revenir naive (SQLite)
        # ou aware (Postgres timezone=True). On aligne les deux en UTC aware.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current < expires
