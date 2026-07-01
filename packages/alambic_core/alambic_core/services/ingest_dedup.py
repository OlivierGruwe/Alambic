"""alambic_core.services.ingest_dedup — dédoublonnage temporel des imports FTP/S3.

Décide si un fichier source doit être importé ou ignoré, selon une fenêtre de
temps glissante : un fichier vu il y a moins de `window` minutes est ignoré (déjà
importé récemment, pas encore déplacé) ; au-delà, il est ré-importable (redépôt
légitime). Enregistre chaque import et purge les entrées périmées.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alambic_core.models import IngestRegistry

# Fenêtre glissante par défaut (minutes) : un fichier vu il y a moins que ça est
# ignoré. Choisie bien au-dessus de l'intervalle de balayage (5 min) pour couvrir
# les cas où le déplacement vers treated/ échoue plusieurs cycles de suite.
DEFAULT_WINDOW_MINUTES = 60


def should_import(session, config_id: str | None, source_key: str,
                  *, window_minutes: int = DEFAULT_WINDOW_MINUTES,
                  now: datetime | None = None) -> bool:
    """True si le fichier doit être importé (jamais vu, ou vu il y a assez
    longtemps pour être considéré comme un nouveau dépôt)."""
    now = now or datetime.now(UTC)
    entry = (
        session.query(IngestRegistry)
        .filter(
            IngestRegistry.config_id == config_id,
            IngestRegistry.source_key == source_key,
        )
        .first()
    )
    if entry is None:
        return True

    last = entry.last_seen_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    # Vu récemment (dans la fenêtre) → on n'importe pas.
    return last is None or (now - last) >= timedelta(minutes=window_minutes)


def record_import(session, config_id: str | None, source_key: str, *,
                  source_type: str = "", upload_key: str = "",
                  transaction_id: str | None = None,
                  now: datetime | None = None) -> None:
    """Enregistre (ou met à jour) l'import d'un fichier : pose last_seen_at à
    maintenant. Une seule ligne par (config, chemin) grâce à l'unicité."""
    now = now or datetime.now(UTC)
    entry = (
        session.query(IngestRegistry)
        .filter(
            IngestRegistry.config_id == config_id,
            IngestRegistry.source_key == source_key,
        )
        .first()
    )
    if entry is None:
        entry = IngestRegistry(
            config_id=config_id,
            source_key=source_key,
            source_type=source_type,
            upload_key=upload_key,
            transaction_id=transaction_id,
            last_seen_at=now,
        )
        session.add(entry)
    else:
        entry.last_seen_at = now
        entry.source_type = source_type or entry.source_type
        entry.upload_key = upload_key or entry.upload_key
        if transaction_id:
            entry.transaction_id = transaction_id


def purge_stale(session, *, window_minutes: int = DEFAULT_WINDOW_MINUTES,
                now: datetime | None = None) -> int:
    """Supprime les entrées plus anciennes que la fenêtre (elles n'empêchent plus
    aucun ré-import). Renvoie le nombre de lignes supprimées."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=window_minutes)
    q = session.query(IngestRegistry).filter(IngestRegistry.last_seen_at < cutoff)
    count = q.count()
    if count:
        q.delete(synchronize_session=False)
    return count
