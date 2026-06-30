"""
alambic_core.security.invitations — jetons d'invitation utilisateur.

Un utilisateur créé par un admin n'a pas de mot de passe : il reçoit un jeton à
usage unique (affiché à l'admin aujourd'hui, envoyé par email demain) qui lui
permet de définir son mot de passe. Le jeton expire après un délai.

Indépendant de Flask : prend une session SQLAlchemy, manipule le modèle User.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from ..models import User
from .passwords import hash_password

# Durée de validité d'une invitation.
INVITE_TTL = timedelta(days=7)


def generate_invite_token() -> str:
    """Jeton d'invitation cryptographiquement sûr (URL-safe)."""
    return secrets.token_urlsafe(32)


def issue_invitation(session, user: User) -> str:
    """Génère (ou régénère) un jeton d'invitation pour un utilisateur.

    Renvoie le jeton en clair (à transmettre à l'utilisateur). Le mot de passe
    de l'utilisateur est laissé vide jusqu'à ce qu'il définisse le sien.
    """
    token = generate_invite_token()
    user.invite_token = token
    user.invite_expires_at = datetime.now(UTC) + INVITE_TTL
    session.commit()
    return token


def _as_aware(dt: datetime | None) -> datetime | None:
    """Normalise en datetime tz-aware (SQLite peut renvoyer du naïf)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def find_valid_invitation(session, token: str) -> User | None:
    """Retrouve l'utilisateur d'un jeton d'invitation valide (non expiré)."""
    if not token:
        return None
    from sqlalchemy import select

    user = session.scalars(select(User).where(User.invite_token == token)).first()
    if user is None:
        return None
    expires = _as_aware(user.invite_expires_at)
    if expires is None or expires < datetime.now(UTC):
        return None
    return user


def accept_invitation(session, token: str, new_password: str) -> User | None:
    """Consomme une invitation : pose le mot de passe, efface le jeton.

    Renvoie l'utilisateur si succès, None si le jeton est invalide/expiré.
    """
    user = find_valid_invitation(session, token)
    if user is None:
        return None
    user.password_hash = hash_password(new_password)
    user.invite_token = None
    user.invite_expires_at = None
    user.active = True
    session.commit()
    return user
