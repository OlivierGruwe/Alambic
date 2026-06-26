# Déploiement Alambic — séquence d'initialisation

Pour un déploiement NEUF (base vide), dans l'ordre :

## 1. Infrastructure
```bash
docker compose up -d        # Postgres (5433), RabbitMQ, Redis, Garage
```

## 2. Variables d'environnement (.env)
```
ALAMBIC_DATABASE_URL=postgresql+psycopg://alambic:alambic@127.0.0.1:5433/alambic
ALAMBIC_SECRET_KEY=<clé Fernet — générer une fois, garder stable>
```
Générer la clé Fernet :
```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 3. Schéma (migration initiale unique)
```bash
cd packages/alambic_core
uv run alembic upgrade head     # crée les 9 tables
```

## 4. Premier super-admin (bootstrap interactif)
```bash
uv run python -m alambic_core.bootstrap
```
Demande email + mot de passe (masqué). Idempotent : ne fait rien si un
super-admin existe déjà.

## 5. Données de référence (accounts + doctypes)
```bash
uv run python -m alambic_workers.seed.load_reference
```

---

## Migration « from scratch »
L'historique Alembic repart d'une migration initiale unique (`0001_initial`)
qui crée tout le schéma (8 tables métier + users + index unique transaction_key).
Les anciennes révisions (dbec919741c2, a1b2c3d4e5f6) sont supprimées.

Si tu avais déjà appliqué l'ancienne migration, repars d'une base vide :
```bash
# ATTENTION destructif — supprime toutes les données
uv run alembic downgrade base   # ou recréer la base
uv run alembic upgrade head
```
