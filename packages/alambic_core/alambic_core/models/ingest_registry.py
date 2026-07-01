"""alambic_core.models.ingest_registry — registre des fichiers importés (FTP/S3).

Dédoublonnage TEMPOREL des imports depuis une source d'entrée (FTP ou S3). On ne
veut PAS bloquer éternellement un chemin : un même nom de fichier peut être
redéposé légitimement plus tard (ex. « facture.pdf » chaque mois). Ce qu'on évite,
c'est de ré-importer le MÊME fichier dans une fenêtre de temps courte — typiquement
quand le balayage (toutes les 5 min) retombe sur un fichier pas encore déplacé
vers `treated/`.

Mécanisme : chaque import enregistre (config_id, source_key) avec la date du
dernier import (last_seen_at). Au balayage suivant, un fichier est IGNORÉ s'il a
été vu il y a moins que la fenêtre glissante (défaut 60 min) ; au-delà, il est
ré-importable (redépôt considéré comme nouveau). Une tâche de ménage purge les
entrées plus anciennes que la fenêtre : elles ne servent plus au dédoublonnage.

Le déplacement du fichier vers `treated/YYYYMMDD/` reste la protection première
(le fichier quitte le dossier de dépôt) ; le registre est le filet pour les cas
où le déplacement échoue ou où deux balayages se chevauchent.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, TimestampMixin, uuid_str


class IngestRegistry(Base, TimestampMixin):
    """Trace temporelle d'un fichier importé depuis une source d'entrée."""

    __tablename__ = "ingest_registry"
    __table_args__ = (
        # Une seule entrée par (config, chemin) : on met à jour last_seen_at
        # plutôt que d'accumuler des lignes. Le dédoublonnage se décide sur la
        # fraîcheur de last_seen_at (fenêtre glissante), pas sur l'existence.
        UniqueConstraint("config_id", "source_key", name="uq_ingest_config_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)

    config_id: Mapped[str | None] = mapped_column(
        ForeignKey("configs.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Source : « FTP » ou « S3 » (informatif).
    source_type: Mapped[str] = mapped_column(String(8), nullable=False, default="")

    # Clé/chemin du fichier dans la source (chemin FTP distant ou clé objet S3).
    source_key: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    # Date du dernier import de ce fichier : base du dédoublonnage temporel.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )

    # Traçabilité : clé Garage du dépôt + transaction déclenchée.
    upload_key: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    transaction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
