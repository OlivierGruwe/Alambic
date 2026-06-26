# syntax=docker/dockerfile:1
# ═══════════════════════════════════════════════════════════════════════════════
# Alambic — image applicative (workers, poller, beat, UI).
#
# Multi-stage :
#   - base    : Python 3.13 + uv + le monorepo installé. Sert worker normal,
#               poller, beat, UI, et les workers d'étape légère (cab).
#   - office  : base + LibreOffice (apt). Sert le worker de conversion Office et
#               tout worker qui rastérise/convertit (LibreOffice headless).
#
# La même image porte tous les rôles : le RÔLE est choisi par la commande
# (celery -Q <queue>, python -m alambic_workers.trigger.poller, etc.) dans le
# compose, pas par des images différentes. Une image = un artefact à construire.
#
# Build :
#   docker build --target base   -t alambic-app:base   -f docker/Dockerfile .
#   docker build --target office -t alambic-app:office -f docker/Dockerfile .
# (contexte = racine du monorepo, là où vivent packages/ et pyproject.toml)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage commun : Python + uv + dépendances + code ──────────────────────────
FROM python:3.13-slim AS base

# uv : gestionnaire de paquets (copié depuis l'image officielle, version figée).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dépendances système minimales (psycopg a besoin de libpq au runtime).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# On copie d'abord les manifestes pour profiter du cache de couches : tant que
# les pyproject ne changent pas, la résolution des deps n'est pas refaite.
COPY pyproject.toml ./
COPY packages/alambic_core/pyproject.toml packages/alambic_core/
COPY packages/alambic_workers/pyproject.toml packages/alambic_workers/
COPY packages/alambic_ui/pyproject.toml packages/alambic_ui/

# Puis le code des packages.
COPY packages/ packages/

# Installation du workspace (les 3 packages + leurs dépendances).
RUN uv sync --no-dev

# Utilisateur non-root (bonne pratique sécurité ; Swarm-friendly).
RUN useradd -m -u 1000 alambic && chown -R alambic:alambic /app
USER alambic

# Par défaut : worker normal. Surchargé par le compose selon le rôle.
CMD ["celery", "-A", "alambic_workers.celery_app:app", "worker", "-Q", "normal", "--loglevel=INFO"]


# ── Stage office : base + LibreOffice (conversion bureautique) ───────────────
FROM base AS office

USER root

# LibreOffice headless + polices. Sur Debian, apt est plus simple et plus léger
# à maintenir que le RPM téléchargé de FlowerScan. --no-install-recommends évite
# de tirer tout l'environnement de bureau.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-dejavu fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Chemin de soffice fixé pour le code (ALAMBIC_SOFFICE_PATH).
ENV ALAMBIC_SOFFICE_PATH=/usr/bin/soffice

USER alambic

# Par défaut : worker office. Surchargé par le compose si besoin.
CMD ["celery", "-A", "alambic_workers.celery_app:app", "worker", "-Q", "office", "--loglevel=INFO"]
