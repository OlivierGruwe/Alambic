# ═══════════════════════════════════════════════════════════════════════════════
# Alambic — Makefile
# Pipeline de traitement documentaire, 100% souverain (self-hosted FR).
# Stack : Celery (orchestration) + RabbitMQ + PostgreSQL + MinIO + Redis.
# Outils : uv (Python), Docker compose (dev local) / Docker Swarm (prod).
# Environnement cible : Windows + Git Bash (MSYS2). Compatible Linux/macOS.
# ═══════════════════════════════════════════════════════════════════════════════

# ── Réglages shell (CRITIQUE sous Windows/Git Bash) ────────────────────────────
# Force bash : sans ça, make peut invoquer cmd.exe ou sh selon l'install.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
# Une recette = un seul shell : permet d'enchaîner les lignes proprement.
.ONESHELL:
# Évite les surprises de fins de ligne CRLF sur les cibles.
.DELETE_ON_ERROR:

# ── Variables projet ───────────────────────────────────────────────────────────
PROJECT      := alambic
STACK        := alambic
COMPOSE_FILE := docker-compose.yml
SWARM_FILE   := docker-stack.yml
ENV_FILE     := .env
CORE_DIR     := packages/alambic_core

# uv : on appelle tout via `uv run` → pas besoin d'activer .venv,
# transparent entre Windows (.venv/Scripts) et Unix (.venv/bin).
UV  := uv
RUN := $(UV) run

# Compose v2 (plugin docker) par défaut. Override possible : make COMPOSE="docker-compose"
COMPOSE := docker compose

# ── Couleurs (désactivables : make NO_COLOR=1) ─────────────────────────────────
ifndef NO_COLOR
  C_BLUE  := \033[34m
  C_GREEN := \033[32m
  C_AMBER := \033[33m
  C_RED   := \033[31m
  C_DIM   := \033[2m
  C_OFF   := \033[0m
endif

# La cible par défaut affiche l'aide.
.DEFAULT_GOAL := help

# ═══════════════════════════════════════════════════════════════════════════════
# AIDE
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: help
help: ## Affiche cette aide
	@printf "$(C_BLUE)Alambic$(C_OFF) — cibles disponibles :\n\n"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  $(C_GREEN)%-20s$(C_OFF) %s\n", $$1, $$2}'
	@printf "\n$(C_DIM)Astuce : 'make check' d'abord pour vérifier ton environnement.$(C_OFF)\n"

# ═══════════════════════════════════════════════════════════════════════════════
# ENVIRONNEMENT — vérifications & setup
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: check
check: ## Vérifie que uv, docker et make sont installés (Windows/Git Bash inclus)
	@printf "$(C_BLUE)Vérification de l'environnement…$(C_OFF)\n"
	@command -v $(UV) >/dev/null 2>&1 \
		&& printf "  $(C_GREEN)✓$(C_OFF) uv : %s\n" "$$($(UV) --version)" \
		|| { printf "  $(C_RED)✗ uv introuvable.$(C_OFF) Installe-le : winget install astral-sh.uv\n"; exit 1; }
	@command -v docker >/dev/null 2>&1 \
		&& printf "  $(C_GREEN)✓$(C_OFF) docker : %s\n" "$$(docker --version)" \
		|| { printf "  $(C_RED)✗ docker introuvable.$(C_OFF) Installe Docker Desktop (WSL2).\n"; exit 1; }
	@$(COMPOSE) version >/dev/null 2>&1 \
		&& printf "  $(C_GREEN)✓$(C_OFF) docker compose disponible\n" \
		|| printf "  $(C_AMBER)!$(C_OFF) 'docker compose' indisponible — override : make COMPOSE=docker-compose\n"
	@printf "  $(C_GREEN)✓$(C_OFF) make : %s\n" "$$(make --version | head -1)"
	@printf "$(C_GREEN)Environnement OK.$(C_OFF)\n"

.PHONY: env
env: ## Crée le fichier .env depuis .env.example s'il n'existe pas
	@if [ -f "$(ENV_FILE)" ]; then \
		printf "$(C_AMBER)$(ENV_FILE) existe déjà — rien à faire.$(C_OFF)\n"; \
	elif [ -f ".env.example" ]; then \
		cp .env.example "$(ENV_FILE)"; \
		printf "$(C_GREEN)$(ENV_FILE) créé depuis .env.example.$(C_OFF) Pense à le remplir.\n"; \
	else \
		printf "$(C_RED).env.example introuvable.$(C_OFF) (Sera fourni avec la structure projet.)\n"; \
	fi

.PHONY: install
install: check ## Installe les dépendances Python via uv (workspace : projet + packages/*)
	@printf "$(C_BLUE)Installation des dépendances (uv sync, workspace complet)…$(C_OFF)\n"
	$(UV) sync
	@printf "$(C_GREEN)Dépendances installées dans .venv/ (alambic_core inclus)$(C_OFF)\n"

.PHONY: lock
lock: ## Met à jour le lockfile uv (uv.lock)
	$(UV) lock
	@printf "$(C_GREEN)uv.lock mis à jour.$(C_OFF)\n"

# ═══════════════════════════════════════════════════════════════════════════════
# QUALITÉ — lint, format, tests
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: fmt
fmt: ## Formate le code (ruff format)
	$(RUN) ruff format .

.PHONY: lint
lint: ## Vérifie le code (ruff check)
	$(RUN) ruff check .

.PHONY: test
test: ## Tests unitaires rapides (mockés, sans Docker)
	$(RUN) pytest -q -m "not integration"

.PHONY: test-integration
test-integration: ## Tests d'intégration (testcontainers : vrai Postgres, nécessite Docker)
	$(RUN) pytest -q -m integration

.PHONY: test-all
test-all: ## Tous les tests (unitaires + intégration)
	$(RUN) pytest -q

.PHONY: cov
cov: ## Tests avec rapport de couverture
	$(RUN) pytest --cov --cov-report=term-missing

.PHONY: check-all
check-all: lint test ## Lint + tests unitaires (à lancer avant un commit)
	@printf "$(C_GREEN)Tout est vert.$(C_OFF)\n"

# ═══════════════════════════════════════════════════════════════════════════════
# ALAMBIC_CORE — le package partagé (modèles, repos, sécurité, migrations)
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: core-test
core-test: ## Tests unitaires du package alambic_core uniquement
	@$(RUN) pytest $(CORE_DIR)/tests -q -m "not integration" || test $$? -eq 5

.PHONY: core-test-integration
core-test-integration: ## Tests d'intégration d'alambic_core (testcontainers Postgres)
	@$(RUN) pytest $(CORE_DIR)/tests -q -m integration || test $$? -eq 5

.PHONY: core-lint
core-lint: ## Lint du package alambic_core
	$(RUN) ruff check $(CORE_DIR)

.PHONY: db-revision
db-revision: ## Crée une migration Alembic (make db-revision M="message")
	@test -n "$(M)" || { printf "$(C_RED)Précise M=\"message\"$(C_OFF)\n"; exit 1; }
	cd $(CORE_DIR) && $(RUN) alembic revision --autogenerate -m "$(M)"

.PHONY: db-upgrade
db-upgrade: ## Applique les migrations (schéma à jour)
	cd $(CORE_DIR) && $(RUN) alembic upgrade head

.PHONY: db-downgrade
db-downgrade: ## Annule la dernière migration
	cd $(CORE_DIR) && $(RUN) alembic downgrade -1

.PHONY: db-history
db-history: ## Affiche l'historique des migrations
	cd $(CORE_DIR) && $(RUN) alembic history

# ═══════════════════════════════════════════════════════════════════════════════
# DÉVELOPPEMENT LOCAL — Docker compose
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: up
up: ## Démarre la stack en local (docker compose up -d)
	$(COMPOSE) -f $(COMPOSE_FILE) up -d
	@printf "$(C_GREEN)Stack démarrée.$(C_OFF) 'make ps' pour l'état, 'make logs' pour les logs.\n"

.PHONY: down
down: ## Arrête la stack locale
	$(COMPOSE) -f $(COMPOSE_FILE) down

.PHONY: build
build: ## (Re)construit les images locales
	$(COMPOSE) -f $(COMPOSE_FILE) build

.PHONY: ps
ps: ## Liste les services locaux et leur état
	$(COMPOSE) -f $(COMPOSE_FILE) ps

.PHONY: logs
logs: ## Suit les logs (make logs SVC=worker pour un seul service)
	$(COMPOSE) -f $(COMPOSE_FILE) logs -f $(SVC)

.PHONY: restart
restart: down up ## Redémarre la stack locale

# ── Garage (stockage objet S3) ─────────────────────────────────────────────────
.PHONY: garage-secret
garage-secret: ## Génère le rpc_secret + admin_token à coller dans garage.toml
	@printf "$(C_BLUE)rpc_secret  (openssl rand -hex 32) :$(C_OFF)\n"
	@openssl rand -hex 32
	@printf "$(C_BLUE)admin_token (openssl rand -base64 32) :$(C_OFF)\n"
	@openssl rand -base64 32

.PHONY: garage-init
garage-init: ## Initialise Garage : layout single-node + 3 buckets + clé applicative
	@MSYS_NO_PATHCONV=1 bash -c '\
	  set -e; \
	  C="$(COMPOSE) -f $(COMPOSE_FILE) exec -T garage /garage -c /etc/garage.toml"; \
	  echo "→ Attente de Garage…"; \
	  until $$C status >/dev/null 2>&1; do sleep 1; done; \
	  NODE=$$($$C node id -q 2>/dev/null | tail -1 | cut -d@ -f1); \
	  echo "→ Nœud : $$NODE"; \
	  if $$C status 2>&1 | grep -q "NO ROLE ASSIGNED"; then \
	    echo "→ Configuration de la layout (zone fr, 1G)…"; \
	    $$C layout assign "$$NODE" -z fr -c 1G; \
	    $$C layout apply --version 1; \
	  else echo "→ Layout déjà configurée."; fi; \
	  for b in $(or $(B_INPUT),alambic-input) $(or $(B_WORK),alambic-work) $(or $(B_STORAGE),alambic-storage); do \
	    $$C bucket create "$$b" 2>/dev/null && echo "→ Bucket $$b créé." || echo "→ Bucket $$b existe déjà."; \
	  done; \
	  $$C key create $(or $(KEY),alambic-app) 2>/dev/null && echo "→ Clé créée." || echo "→ Clé existe déjà."; \
	  for b in $(or $(B_INPUT),alambic-input) $(or $(B_WORK),alambic-work) $(or $(B_STORAGE),alambic-storage); do \
	    $$C bucket allow --read --write "$$b" --key $(or $(KEY),alambic-app); \
	  done; \
	  echo ""; \
	  printf "$(C_GREEN)Garage initialisé.$(C_OFF) Récupère les identifiants : make garage-keys\n"'

.PHONY: garage-status
garage-status: ## Affiche l'état du cluster Garage
	@MSYS_NO_PATHCONV=1 $(COMPOSE) -f $(COMPOSE_FILE) exec -T garage /garage -c /etc/garage.toml status

.PHONY: garage-keys
garage-keys: ## Affiche les identifiants S3 de la clé applicative (à mettre dans .env)
	@MSYS_NO_PATHCONV=1 $(COMPOSE) -f $(COMPOSE_FILE) exec -T garage /garage -c /etc/garage.toml key info $(or $(KEY),alambic-app) --show-secret

# ── Workers Celery (dev) ───────────────────────────────────────────────────────
.PHONY: worker
worker: ## Lance un worker Celery en local (hors docker) sur la queue Q (def: normal)
	$(RUN) celery -A core.celery_app:app worker -Q $(or $(Q),normal) --loglevel=INFO

.PHONY: beat
beat: ## Lance Celery Beat en local (les crons : billing, sweep, export…)
	$(RUN) celery -A core.celery_app:app beat --loglevel=INFO

.PHONY: flower
flower: ## Monitoring Celery (Flower) sur http://localhost:5555
	$(RUN) celery -A core.celery_app:app flower

# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCTION — Docker Swarm
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: swarm-init
swarm-init: ## Initialise un nœud Swarm (à faire une fois sur le serveur)
	docker swarm init || printf "$(C_AMBER)Swarm déjà initialisé.$(C_OFF)\n"

.PHONY: swarm-deploy
swarm-deploy: ## Déploie la stack sur Swarm (docker stack deploy)
	docker stack deploy -c $(SWARM_FILE) $(STACK)
	@printf "$(C_GREEN)Stack '$(STACK)' déployée sur Swarm.$(C_OFF)\n"

.PHONY: swarm-ps
swarm-ps: ## Liste les services Swarm de la stack
	docker stack services $(STACK)

.PHONY: swarm-scale
swarm-scale: ## Scale un worker (ex: make swarm-scale SVC=worker N=10)
	@test -n "$(SVC)" || { printf "$(C_RED)Précise SVC=…$(C_OFF)\n"; exit 1; }
	@test -n "$(N)"   || { printf "$(C_RED)Précise N=…$(C_OFF)\n"; exit 1; }
	docker service scale $(STACK)_$(SVC)=$(N)

.PHONY: swarm-rm
swarm-rm: ## Retire la stack de Swarm
	docker stack rm $(STACK)

# ═══════════════════════════════════════════════════════════════════════════════
# NETTOYAGE
# ═══════════════════════════════════════════════════════════════════════════════
.PHONY: clean
clean: ## Supprime caches Python et artefacts de build
	@find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -prune -exec rm -rf {} + 2>/dev/null || true
	@printf "$(C_GREEN)Caches nettoyés.$(C_OFF)\n"

.PHONY: clean-all
clean-all: clean ## clean + supprime .venv (réinstall via 'make install')
	@rm -rf .venv
	@printf "$(C_GREEN).venv supprimé.$(C_OFF)\n"

workers-test:
	uv run pytest packages/alambic_workers/tests/ -v

workers-fmt:
	uv run ruff check --fix packages/alambic_workers/
	uv run ruff format packages/alambic_workers/