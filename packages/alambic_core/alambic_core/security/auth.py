"""
alambic_core.security.auth — abstraction d'authentification.

Le reste de l'application (webapp admin, API) ne dépend QUE de l'interface
AuthProvider, jamais d'une implémentation concrète. Aujourd'hui :
LocalAuthProvider (identités dans PostgreSQL, mots de passe argon2). Demain,
sans toucher au code appelant : KeycloakAuthProvider (délégation OIDC).

C'est le même principe que SecretProvider pour le chiffrement : une couture
d'extension propre, décidée dès le départ.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy import select

from ..models import User
from .passwords import needs_rehash, verify_password


class AuthProvider(ABC):
    """Contrat d'un fournisseur d'authentification."""

    @abstractmethod
    def authenticate(self, email: str, password: str) -> User | None:
        """Renvoie l'utilisateur si les identifiants sont valides, sinon None."""

    @abstractmethod
    def get_user(self, user_id: str) -> User | None:
        """Recharge un utilisateur par son id (pour le user_loader de session)."""


class LocalAuthProvider(AuthProvider):
    """Authentification locale : table users + argon2.

    La session SQLAlchemy est fournie par une factory (callable renvoyant un
    context manager de session), pour rester découplé du cycle de vie Flask.
    """

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def authenticate(self, email: str, password: str) -> User | None:
        email = (email or "").strip().lower()
        if not email or not password:
            return None
        with self._session_factory() as session:  # type: Session
            user = session.scalars(select(User).where(User.email == email)).first()
            if user is None or not user.active:
                return None
            if user.auth_provider != "local":
                # Compte délégué à un IdP externe : pas d'auth locale possible.
                return None
            if not verify_password(user.password_hash, password):
                return None
            # Montée en sécurité transparente si les paramètres argon2 ont évolué.
            if needs_rehash(user.password_hash):
                from .passwords import hash_password

                user.password_hash = hash_password(password)
                session.commit()
            session.expunge(user)
            return user

    def get_user(self, user_id: str) -> User | None:
        if not user_id:
            return None
        with self._session_factory() as session:
            user = session.get(User, user_id)
            if user is not None:
                session.expunge(user)
            return user
