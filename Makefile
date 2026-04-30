# qdrant_rag — developer Makefile
#
# Run `make` or `make help` for the full command list.
#
# Two stack modes:
#   - prod  (default): no override; postgres/redis container-internal only.
#                       Coexists with host Postgres/Redis (e.g., DynamicADK).
#                       Web on http://localhost:${HTTP_PORT:-8080}/.
#   - dev:               with override; bind mount + hot reload.
#                       Exposes postgres on 5432 and redis on 6379 to host —
#                       conflicts with host services. Stop host postgresql /
#                       redis-server first if you want to use this mode.

-include .env
export

# Pin compose invocations explicitly so we don't mix prod/dev by accident.
COMPOSE_PROD := docker compose -f docker-compose.yml
COMPOSE_DEV  := docker compose

WEB_PORT ?= $(or $(HTTP_PORT),8080)

# When the first goal is `run` or `bash`, suppress secondary-goal target bodies
# so `make run python manage.py migrate` doesn't also trigger the `migrate:` target.
ifneq ($(filter run bash,$(firstword $(MAKECMDGOALS))),)
SKIP_AS_RUN_ARG := 1
endif

.DEFAULT_GOAL := help

.PHONY: help \
        up down restart rebuild wipe logs ps health \
        dev-up dev-down dev-logs \
        bge-download \
        bash run \
        migrate makemigrations makemigrations-host migrations-check \
        shell superuser \
        psql redis-cli \
        snapshot backup load-test \
        test test-cov lint format format-check check \
        clean

# ────────────────────────────────────────────────────────────────────
# Help (default)
# ────────────────────────────────────────────────────────────────────

help:
	@printf "\n  qdrant_rag — common dev commands\n\n"
	@printf "  \033[1mStack lifecycle (prod mode — coexists with DynamicADK + host services):\033[0m\n"
	@printf "    make up                Build and start the stack on host port $(WEB_PORT)\n"
	@printf "    make down              Stop the stack (keeps volumes)\n"
	@printf "    make restart           down + up\n"
	@printf "    make rebuild           Rebuild images + restart (KEEPS volumes — fast)\n"
	@printf "    make wipe              down -v + up --build (WIPES all volumes — slow; re-downloads BGE-M3)\n"
	@printf "    make logs              Tail the web container's logs\n"
	@printf "    make ps                Show all container statuses\n"
	@printf "    make health            curl /healthz and pretty-print the JSON\n\n"
	@printf "  \033[1mBGE-M3 model cache (host bind mount at ./.bge_cache/):\033[0m\n"
	@printf "    make bge-download      Wipe ./.bge_cache/ and re-download BGE-M3 (~4.5GB incl. ONNX; one-time)\n\n"
	@printf "  \033[1mStack lifecycle (dev mode — bind mount + hot reload):\033[0m\n"
	@printf "    make dev-up            Up with override (hot reload; conflicts with host pg/redis)\n"
	@printf "    make dev-down          Down with override\n"
	@printf "    make dev-logs          Tail web logs in dev mode\n\n"
	@printf "  \033[1mIn-container command runner (uv-like ergonomics):\033[0m\n"
	@printf "    make bash                          Drop into a bash shell inside the web container\n"
	@printf "    make run <cmd> [args...]           Run any command inside the web container\n"
	@printf "                                       e.g.  make run python manage.py migrate\n"
	@printf "                                             make run pytest -v\n"
	@printf "                                             make run python -c 'import sys; print(sys.version)'\n"
	@printf "    make run CMD='<cmd>'               Same, but for commands containing -- flags or shell metacharacters\n"
	@printf "                                       e.g.  make run CMD='python manage.py migrate --noinput'\n\n"
	@printf "  \033[1mDatabase / migrations:\033[0m\n"
	@printf "    make migrate                       Apply pending migrations (in web container)\n"
	@printf "    make makemigrations APP=<app>      Generate migrations (in web container)\n"
	@printf "    make makemigrations-host APP=<app> Generate migrations from host (no Docker)\n"
	@printf "    make migrations-check              Verify no pending migrations\n"
	@printf "    make shell                         Django shell inside web container\n"
	@printf "    make superuser                     Create admin/admin superuser (non-interactive)\n"
	@printf "    make psql                          psql into qdrant_rag's Postgres\n"
	@printf "    make redis-cli                     redis-cli into qdrant_rag's Redis\n\n"
	@printf "  \033[1mQuality (run on host):\033[0m\n"
	@printf "    make test              uv run pytest\n"
	@printf "    make test-cov          uv run pytest with coverage report\n"
	@printf "    make lint              uv run ruff check .\n"
	@printf "    make format            uv run ruff format . (auto-fix)\n"
	@printf "    make format-check      uv run ruff format --check .\n"
	@printf "    make check             lint + format-check + migrations-check + test\n\n"
	@printf "  \033[1mCleanup:\033[0m\n"
	@printf "    make clean             Remove __pycache__, .pyc, .pytest_cache, .ruff_cache\n\n"

# ────────────────────────────────────────────────────────────────────
# Stack lifecycle — prod mode (default; no override)
# ────────────────────────────────────────────────────────────────────

up:
	$(COMPOSE_PROD) up -d --build
	@echo ""
	@echo "  Stack starting. Wait ~60s for healthchecks, then:"
	@echo "    make health     # verify"
	@echo "    make logs       # tail web logs"
	@echo ""

down:
	$(COMPOSE_PROD) down

restart: down up

rebuild:
	$(COMPOSE_PROD) up -d --build --force-recreate

wipe:
	$(COMPOSE_PROD) down -v
	$(COMPOSE_PROD) up -d --build

# ────────────────────────────────────────────────────────────────────
# BGE-M3 model cache — host bind mount at ./.bge_cache/
# ────────────────────────────────────────────────────────────────────

bge-download:
	@if [ -d .bge_cache ]; then \
		printf "  Removing existing .bge_cache/ ... "; \
		rm -rf .bge_cache; \
		echo "done."; \
	fi
	@mkdir -p .bge_cache
	@echo "  Downloading BAAI/bge-m3 to ./.bge_cache/ (~4.5GB incl. ONNX; takes 5-15 min) ..."
	uv run python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-m3', cache_dir='./.bge_cache')"
	@printf "  ✓ Done. Cache size: "
	@du -sh .bge_cache | awk '{print $$1}'
	@echo "    Restart the stack to pick it up:  make rebuild"

logs:
	$(COMPOSE_PROD) logs -f web

ps:
	$(COMPOSE_PROD) ps

health:
	@curl -fsS http://localhost:$(WEB_PORT)/healthz | python -m json.tool || \
		(echo ""; echo "  ✗ healthz failed. Stack may not be up yet — try: make ps"; exit 1)

# ────────────────────────────────────────────────────────────────────
# Backups + load test (Phase 8b)
# ────────────────────────────────────────────────────────────────────

snapshot:
	@echo "  Taking Qdrant snapshot (rotation: $${QDRANT_SNAPSHOT_KEEP:-7}); see RUNBOOK §8."
	bash scripts/snapshot_qdrant.sh

backup:
	@echo "  Taking Postgres backup (rotation: $${POSTGRES_BACKUP_KEEP:-14}); see RUNBOOK §7."
	bash scripts/backup_postgres.sh

load-test:
	@echo "  Running load test against the stack at $${LOAD_TEST_URL:-http://localhost:$(WEB_PORT)}."
	@echo "  Stack must be up; see RUNBOOK §6."
	uv run python scripts/load_test.py --url $${LOAD_TEST_URL:-http://localhost:$(WEB_PORT)}

# ────────────────────────────────────────────────────────────────────
# Stack lifecycle — dev mode (override; hot reload)
# ────────────────────────────────────────────────────────────────────

dev-up:
	@if ss -ltnp 'sport = :5432 or sport = :6379' 2>/dev/null | grep -qE 'postgres|redis-server'; then \
		echo "  ✗ Host Postgres or Redis is on 5432/6379 — dev mode would conflict."; \
		echo "    Run: sudo systemctl stop postgresql redis-server"; \
		echo "    Or use 'make up' (prod mode) which coexists with host services."; \
		exit 1; \
	fi
	$(COMPOSE_DEV) up -d --build

dev-down:
	$(COMPOSE_DEV) down

dev-logs:
	$(COMPOSE_DEV) logs -f web

# ────────────────────────────────────────────────────────────────────
# In-container command runner (uv-like ergonomics)
# ────────────────────────────────────────────────────────────────────

# Drop into a bash shell inside the web container.
bash:
	$(COMPOSE_PROD) exec -it web bash

# Run any command inside the web container.
# Two forms:
#   make run <cmd> [args...]       — uv-like, e.g. `make run pytest -v`
#   make run CMD='<cmd with flags>' — escape hatch for -- flags or shell metachars,
#                                     e.g. `make run CMD='python manage.py migrate --noinput'`
#
# Caveat: if a positional arg matches an existing Make target name (e.g. `shell`,
# `migrate`), Make may also run that target. Use the CMD= form to avoid this,
# or just run the existing convenience target instead (`make shell`, `make migrate`).
run:
	@$(COMPOSE_PROD) exec -it web $(if $(CMD),sh -c "$(CMD)",$(filter-out $@,$(MAKECMDGOALS)))

# Catch-all so positional args after `make run ...` don't error as missing targets.
# Only matches targets that don't have an explicit rule.
%:
	@:

# ────────────────────────────────────────────────────────────────────
# Database / migrations
# ────────────────────────────────────────────────────────────────────

migrate:
ifndef SKIP_AS_RUN_ARG
	$(COMPOSE_PROD) exec web python manage.py migrate
else
	@:
endif

makemigrations:
ifndef SKIP_AS_RUN_ARG
	@if [ -z "$(APP)" ]; then \
		echo "Usage: make makemigrations APP=<app_name>   (e.g. APP=tenants)"; \
		exit 1; \
	fi
	$(COMPOSE_PROD) exec web python manage.py makemigrations $(APP)
else
	@:
endif

# Generate migrations from the host without bouncing the web container.
# Uses tests.test_settings (SQLite overlay) — no DB connection needed for makemigrations.
makemigrations-host:
	@if [ -z "$(APP)" ]; then \
		echo "Usage: make makemigrations-host APP=<app_name>"; \
		exit 1; \
	fi
	DJANGO_SETTINGS_MODULE=tests.test_settings uv run python manage.py makemigrations $(APP)

migrations-check:
	$(COMPOSE_PROD) exec web python manage.py makemigrations --check --dry-run

shell:
ifndef SKIP_AS_RUN_ARG
	$(COMPOSE_PROD) exec web python manage.py shell
else
	@:
endif

superuser:
	$(COMPOSE_PROD) exec -e DJANGO_SUPERUSER_PASSWORD=admin web \
		python manage.py createsuperuser --noinput \
		--username admin --email admin@local || true
	@echo ""
	@echo "  Superuser ready: admin / admin"
	@echo "  Login at: http://localhost:$(WEB_PORT)/admin/"
	@echo ""

psql:
	$(COMPOSE_PROD) exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

redis-cli:
	$(COMPOSE_PROD) exec redis redis-cli

# ────────────────────────────────────────────────────────────────────
# Quality
# ────────────────────────────────────────────────────────────────────

test:
	uv run pytest -v

test-cov:
	uv run pytest --cov=apps --cov-report=term-missing

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

check: lint format-check migrations-check test

# ────────────────────────────────────────────────────────────────────
# Cleanup
# ────────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -not -path './.venv/*' -delete
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	@echo "  Caches cleared."
