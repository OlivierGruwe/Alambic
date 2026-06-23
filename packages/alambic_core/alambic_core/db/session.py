"""
alambic_core.db.session — moteur, sessions, et bootstrap.

Point d'entrée pour obtenir une session SQLAlchemy liée à PostgreSQL.
Configure aussi le provider de chiffrement au démarrage (à appeler une fois).

Usage typique dans un worker :
    from alambic_core.db.session import init_core, session_scope
    init_core()                      # une fois au démarrage du process
    with session_scope() as s:       # par unité de travail
        repo = DocumentRepository(s)
        ...
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import CoreSettings
from .types import set_secret_provider
from ..security.fernet_provider import FernetSecretProvider

# Singletons de module (initialisés par init_core / get_engine).
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_core(settings: CoreSettings | None = None) -> None:
    """Initialise le moteur DB et le provider de chiffrement.

    À appeler UNE FOIS au démarrage du process (worker, API, script).
    Idempotent : un second appel reconfigure proprement.
    """
    global _engine, _SessionLocal
    settings = settings or CoreSettings()

    # Provider de chiffrement (obligatoire : les modèles ont des colonnes chiffrées)
    if not settings.secret_keys:
        raise RuntimeError(
            "ALAMBIC_SECRET_KEY manquant : les secrets ne peuvent pas être chiffrés. "
            'Génère une clé : python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    set_secret_provider(FernetSecretProvider(settings.secret_keys))

    # Moteur PostgreSQL. pool_pre_ping : vérifie la connexion avant usage
    # (évite les 'server closed the connection' après inactivité — production).
    _engine = create_engine(
        settings.database_url,
        echo=settings.sql_echo,
        pool_pre_ping=True,
        future=True,
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Core non initialisé. Appelle init_core() au démarrage.")
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    if _SessionLocal is None:
        raise RuntimeError("Core non initialisé. Appelle init_core() au démarrage.")
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager : commit si tout va bien, rollback sinon, ferme toujours.

    C'est l'unité de travail standard. Remplace le save()/create()/update()
    manuel de FclObject par une transaction explicite et sûre.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
