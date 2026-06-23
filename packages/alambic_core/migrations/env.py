"""
Environnement Alembic pour alambic_core.

Particularités :
  - L'URL DB vient de CoreSettings (env ALAMBIC_DATABASE_URL), pas d'alembic.ini.
  - On configure un SecretProvider AVANT d'importer les modèles : EncryptedString
    en a besoin à la définition des colonnes chiffrées. En contexte migration,
    un NullSecretProvider suffit (on ne lit/écrit pas de secret, on crée le schéma).
  - target_metadata = Base.metadata pour l'autogenerate.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 1) Provider de chiffrement (Null en migration : on manipule le schéma, pas les données)
from alambic_core.db.types import set_secret_provider
from alambic_core.security.fernet_provider import NullSecretProvider

set_secret_provider(NullSecretProvider())

# 2) Settings + métadonnées des modèles
import alambic_core.models  # noqa: E402,F401 — enregistre les 8 modèles sur Base.metadata
from alambic_core.db.base import Base  # noqa: E402
from alambic_core.db.config import CoreSettings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Injecte l'URL depuis l'environnement (jamais en dur dans alembic.ini)
_settings = CoreSettings()
config.set_main_option("sqlalchemy.url", _settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Mode offline : génère le SQL sans connexion (utile pour relecture)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Mode online : applique réellement contre la base."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # détecte les changements de type de colonne
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
