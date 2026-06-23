# Alambic

Pipeline de traitement documentaire, 100% souverain (self-hosted, hébergeable en France).

Migration d'une architecture AWS serverless (Lambda + Step Functions + DynamoDB +
S3 + SQS) vers une stack open-source déployable sur Docker Swarm.

## Stack

| Rôle | Composant | Remplace (AWS) |
|------|-----------|----------------|
| Orchestration / workers | Celery | Step Functions + Lambda |
| Broker de messages | RabbitMQ | SQS |
| Backend résultats | Redis | — |
| Base de données | PostgreSQL | DynamoDB |
| Stockage objet | MinIO | S3 |
| Crons | Celery Beat | EventBridge Scheduler |
| Métriques | Prometheus + Grafana | CloudWatch |
| Monitoring workers | Flower | — |

## Démarrage rapide

Prérequis : `uv`, Docker Desktop, `make` (voir `make check`).

```bash
make check      # vérifie l'environnement
make env        # crée .env depuis .env.example
make install    # installe les dépendances Python (uv sync)
make up         # démarre l'infra (MinIO, PostgreSQL, RabbitMQ, Redis)
```

Toutes les commandes passent par le Makefile : `make help` pour la liste.

## Structure

- `core/` — socle transverse (config, accès DB, stockage, observabilité)
- `tasks/` — le travail unitaire (ex-Lambdas) en tasks Celery
- `orchestration/` — l'enchaînement des workflows (ex-Step Functions)
- `db/` — migrations Alembic du schéma PostgreSQL
- `scripts/` — scripts d'init (MinIO, PostgreSQL)
- `docker/` — Dockerfiles des images custom
- `deploy/` — configs Prometheus / Grafana
