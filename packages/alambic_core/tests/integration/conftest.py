"""
Fixtures des tests d'INTÉGRATION (vrai PostgreSQL via testcontainers).

Contrairement aux tests unitaires (SQLite en mémoire), ceux-ci montent un vrai
Postgres jetable dans un conteneur Docker. Ils valident ce que SQLite ne peut
pas : JSONB natif, index GIN, contraintes ondelete, CheckConstraint serveur.

Nécessite Docker. Marqués @pytest.mark.integration → exclus de `make test`,
lancés par `make test-integration`.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# testcontainers est une dépendance de dev (cf. pyproject). Import paresseux
# pour que la collecte ne casse pas si on lance juste les tests unitaires.
testcontainers_postgres = pytest.importorskip(
    "testcontainers.postgres", reason="testcontainers requis pour les tests d'intégration"
)
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_engine():
    """Démarre un PostgreSQL jetable, crée le schéma via les modèles, le détruit.

    scope=module : un seul conteneur pour tout le fichier (rapide). Le conteneur
    est arrêté et supprimé automatiquement à la fin.
    """
    # Provider de chiffrement (les modèles ont des colonnes chiffrées)
    from alambic_core.db.types import set_secret_provider
    from alambic_core.security.fernet_provider import FernetSecretProvider

    set_secret_provider(FernetSecretProvider(Fernet.generate_key().decode()))

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())

        # On crée le schéma directement depuis les modèles (create_all) plutôt
        # que via Alembic : plus simple en test, et ça valide que les modèles
        # produisent un schéma PostgreSQL valide (JSONB, contraintes…).
        import alambic_core.models  # noqa: F401 — enregistre les modèles
        from alambic_core.db.base import Base

        Base.metadata.create_all(engine)

        yield engine
        engine.dispose()


@pytest.fixture
def pg_session(pg_engine):
    """Session liée au Postgres de test, rollback après chaque test pour isoler."""
    with Session(pg_engine) as s:
        yield s
        s.rollback()
