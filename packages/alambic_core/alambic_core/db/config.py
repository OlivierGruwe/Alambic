"""
alambic_core.db.config — configuration via variables d'environnement.

pydantic-settings lit l'environnement (et le .env) avec validation de types.
Centralise tout ce dont la couche données a besoin : URL PostgreSQL et clé(s)
de chiffrement des secrets.

IMPORTANT — résolution du .env :
On pointe explicitement vers le .env à la RACINE du projet, calculé depuis
l'emplacement de ce fichier. Sans ça, lancer une commande depuis un
sous-dossier (ex: `cd packages/alambic_core && alembic …`) ferait chercher le
.env au mauvais endroit, et pydantic retomberait sur les valeurs par défaut.
Les vraies variables d'environnement (export ALAMBIC_…) priment toujours sur
le .env, donc on peut surcharger ponctuellement.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ce fichier : <racine>/packages/alambic_core/alambic_core/db/config.py
# parents[0]=db  [1]=alambic_core  [2]=alambic_core(pkg)  [3]=packages  [4]=racine
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_ENV_FILE = _PROJECT_ROOT / ".env"


class CoreSettings(BaseSettings):
    """Réglages d'alambic_core. Préfixe ALAMBIC_ pour éviter les collisions."""

    model_config = SettingsConfigDict(
        env_prefix="ALAMBIC_",
        # Chemin ABSOLU vers le .env racine (indépendant du répertoire courant).
        # On tente aussi ".env" en repli (utile si la structure diffère).
        env_file=(str(_ENV_FILE), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # ignore les autres variables du .env (broker, garage…)
    )

    # URL de connexion PostgreSQL (psycopg v3).
    # PAS de valeur par défaut « plausible » : un défaut avec un faux mot de
    # passe masquait un .env non chargé en produisant une erreur d'auth obscure.
    # Vide => message clair « config manquante » au démarrage.
    database_url: str = Field(default="")

    # Clé(s) de chiffrement Fernet. Plusieurs clés séparées par des virgules
    # permettent la rotation (la première chiffre, toutes déchiffrent).
    # Génération : python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_key: str = Field(default="")

    # Echo SQL (debug) — à laisser False en prod.
    sql_echo: bool = Field(default=False)

    @property
    def secret_keys(self) -> list[str]:
        """Liste des clés de chiffrement (support rotation via virgules)."""
        return [k.strip() for k in self.secret_key.split(",") if k.strip()]
