"""
alambic_core.db.base — fondations SQLAlchemy 2.0.

Remplace la classe maison FclObject. Les primitives que FclObject
réimplémentait à la main par-dessus DynamoDB deviennent natives :
    self.version + try_set_status()    → VersionMixin (version_id_col)
    self.creation_date                 → TimestampMixin.created_at
    self.last_modification_date        → TimestampMixin.updated_at (onupdate)
    self.author / self.actor           → AuditMixin
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base déclarative commune à tous les modèles alambic_core."""

    pass


def uuid_str() -> str:
    """Identifiant string (comme FclObject.get_id) — minimise les changements
    dans le code métier qui manipule des IDs en str."""
    return str(uuid.uuid4())


class TimestampMixin:
    """Horodatage auto — remplace creation_date / last_modification_date.

    server_default=func.now() : Postgres pose la date à l'INSERT.
    onupdate=func.now()       : SQLAlchemy met à jour à chaque UPDATE.
    """

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditMixin:
    """Traçabilité — remplace author / actor.

    Plus de valeur sentinelle "system" (qu'imposait un GSI DynamoDB) :
    author est simplement nullable.
    """

    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)


class VersionMixin:
    """Versioning optimiste natif — remplace self.version + try_set_status().

    SQLAlchemy incrémente `version` à chaque UPDATE et lève StaleDataError
    si deux processus modifient la même ligne en concurrence. C'est le
    compare-and-swap que tu faisais à la main, géré par l'ORM.

    Le modèle qui hérite de ce mixin doit déclarer :
        __mapper_args__ = {"version_id_col": <son attribut version>}
    (SQLAlchemy exige que version_id_col pointe la colonne de CE modèle).
    """

    version: Mapped[int] = mapped_column(nullable=False, default=1)
