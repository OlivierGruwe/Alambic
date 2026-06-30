"""alambic_core.models.user — modèle User (authentification + rôles).

Remplace l'auth Cognito de FlowerScan par une identité souveraine, stockée
dans PostgreSQL. Le mot de passe n'est JAMAIS stocké en clair : seul son hash
argon2 est persisté (password_hash).

Ouverture Keycloak : les champs external_id / auth_provider permettent de
rattacher un utilisateur à un fournisseur d'identité externe sans changer le
schéma. En mode local, auth_provider = "local" et password_hash est renseigné ;
en mode Keycloak, auth_provider = "keycloak", external_id = le sub OIDC, et
password_hash reste vide (l'auth est déléguée).
"""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str
from ..domain.enums import UserRole


class User(Base, TimestampMixin, AuditMixin):
    """Utilisateur de la plateforme (admin ou valideur)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    # Identité
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Authentification locale (argon2). Vide si auth déléguée (Keycloak).
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Rôle + rattachement compte. account_id nullable : un SUPER_ADMIN est
    # transverse (pas rattaché à un compte unique).
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.VALIDATOR.value)
    account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=True, index=True
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @validates("email")
    def _normalize_email(self, key: str, value: str) -> str:
        """Normalise l'email en minuscules sans espaces, à l'écriture.

        Garantit la cohérence entre stockage, unicité et recherche (by_email) :
        'Boss@X.com' et 'boss@x.com' désignent le même compte.
        """
        return (value or "").strip().lower()

    # ── Ouverture vers un fournisseur d'identité externe (Keycloak) ──────────
    # "local" aujourd'hui ; "keycloak" demain, sans migration de schéma.
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # ── Invitation (définition du mot de passe par l'utilisateur) ────────────
    # Un utilisateur créé par un admin n'a pas encore de mot de passe : il reçoit
    # un jeton à usage unique (affiché à l'admin, ou envoyé par email plus tard)
    # qui lui permet de définir son mot de passe via /invitation/<token>.
    invite_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    invite_expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Helpers de rôle (lisibilité dans les blueprints / décorateurs) ───────
    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN.value

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.SUPER_ADMIN.value, UserRole.ADMIN.value)

    @property
    def is_validator(self) -> bool:
        return self.role == UserRole.VALIDATOR.value

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role}>"
