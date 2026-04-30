# Phase 1 — Project Foundation

> **Audience:** A coding agent (e.g. Claude Code) building this from a clean working directory at `/home/bol7/Documents/BOL7/Qdrant`. Only `README.md`, `rag_system_guide.md`, and this `build_prompts/` folder exist when you start.

---

## Mission

Build the project skeleton, infra layer, and Docker Compose stack for the **qdrant_rag** service. After this phase the stack should boot, all containers pass healthchecks, and a `/healthz` HTTP endpoint reports the state of subsystems available so far (Postgres + Qdrant). No business logic, no domain models, no API routes other than `/healthz`. Subsequent phases will fill in the rest.

This is a **production-ready** foundation, not a prototype. Cross-cutting concerns (structured logging, env-driven config, container healthchecks, retry-on-startup) ship in this phase and stay forever. Do not cut corners.

---

## Read first

- `README.md` — project overview and architecture (already present)
- `rag_system_guide.md` — full design rationale (reference; do not implement features beyond Phase 1's scope)

---

## Hard constraints (read before writing any code)

1. **Python version: 3.13.** Use `uv` (Astral) for everything — never `pip` or `poetry` directly. `uv` manages the venv automatically.
2. **Torch must be CPU-only.** It is **not** installed in Phase 1 (Phase 4 owns it). When you write `pyproject.toml`, leave torch and FlagEmbedding out of dependencies for now — but include the `[[tool.uv.index]]` config for `pytorch-cpu` so Phase 4 can add them without restructuring.
3. **No CUDA, no GPU.** Anywhere. Ever.
4. **Do not generate gRPC stubs.** The `proto/` directory is created empty (with `.gitkeep`) and `scripts/compile_proto.sh` exists as a stub — Phase 7 will fill it in. The `grpc` Compose service will start with a placeholder command (`sleep infinity`) and depend on the `web` healthcheck so it doesn't crash-loop.
5. **No authentication.** DRF defaults: `DEFAULT_PERMISSION_CLASSES = ["rest_framework.permissions.AllowAny"]`. Django admin still requires a superuser.
6. **No business endpoints.** No `/v1/...` routes yet. Only `/healthz` and `/admin/`.
7. **No models.** Migrations directory is created but empty for each app. Phase 2 owns Tenant/Bot/Document.
8. **Never commit `.env`.** Only `.env.example` with placeholder values goes in git. The user fills `.env` locally.
9. **Strictly use the file paths and names listed below.** Future phases reference them.

---

## Stack & versions (locked, do not change)

| Component | Version | Notes |
|---|---|---|
| Python | 3.13 | `.python-version` file pins it |
| uv | latest | Astral's official installer / `pip install uv` |
| Django | `>=5.2,<6.0` | LTS |
| DRF | `>=3.16` | |
| Postgres | 16-alpine | Docker image `postgres:16-alpine` |
| Redis | 7-alpine | Docker image `redis:7-alpine` |
| Qdrant | `v1.17.1` | Docker image `qdrant/qdrant:v1.17.1` (pinned, never `latest`) |
| psycopg | `[binary,pool]` | psycopg3, not psycopg2 |
| structlog | latest | Structured logging |
| django-environ | latest | Env-driven settings |
| celery | `>=5.4` | Wired but not used until v2 |
| redis (Python) | `>=5.0` | Celery broker client |
| qdrant-client | latest, **without** `[fastembed]` extra | Phase 3 will use this |
| pytest, pytest-django | latest | Dev deps |
| ruff, mypy, django-stubs | latest | Dev deps |

---

## Deliverables

Create exactly these files. Tree:

```
qdrant_rag/  (= /home/bol7/Documents/BOL7/Qdrant)
├── .dockerignore
├── .env.example
├── .gitignore
├── .python-version
├── Dockerfile
├── README.md                       (already exists — do not modify)
├── docker-compose.yml
├── docker-compose.override.yml
├── manage.py
├── pyproject.toml
├── rag_system_guide.md             (already exists — do not modify)
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── config/
│   ├── __init__.py
│   ├── asgi.py
│   ├── celery.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── apps/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── logging.py
│   │   ├── urls.py
│   │   └── views.py
│   ├── tenants/
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   └── migrations/
│   │       └── __init__.py
│   ├── documents/
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   └── migrations/
│   │       └── __init__.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   └── apps.py
│   ├── qdrant_core/
│   │   ├── __init__.py
│   │   └── apps.py
│   └── grpc_service/
│       ├── __init__.py
│       └── apps.py
│
├── proto/
│   └── .gitkeep
│
├── scripts/
│   ├── compile_proto.sh
│   └── verify_setup.py
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_settings.py        # in-memory SQLite settings overlay for host-side test runs
│   └── test_healthz.py
│
└── build_prompts/
    └── phase_1_foundation.md       (this file — do not modify)
```

### File-by-file specification

#### `.python-version`

```
3.13
```

#### `pyproject.toml`

- Project name: `qdrant_rag`, version `0.1.0`, requires-python `>=3.13`
- Description: "Multi-tenant Qdrant vector storage service for RAG."
- Production dependencies (only what Phase 1 needs):
  - `django>=5.2,<6.0`
  - `djangorestframework>=3.16`
  - `django-environ`
  - `psycopg[binary,pool]`
  - `redis>=5.0`
  - `celery>=5.4`
  - `qdrant-client` (no extras — do **not** add `[fastembed]`)
  - `structlog`
  - `gunicorn`
- Dev dependencies (`[dependency-groups.dev]`):
  - `pytest`
  - `pytest-django`
  - `pytest-cov`
  - `ruff`
  - `mypy`
  - `django-stubs[compatible-mypy]`
  - `httpx` (for testing the healthz endpoint)
- Include this block (required for Phase 4 — leave it now so dependencies don't need restructuring later):

  ```toml
  [[tool.uv.index]]
  name = "pytorch-cpu"
  url = "https://download.pytorch.org/whl/cpu"
  explicit = true
  ```

- Configure `pytest`:

  ```toml
  [tool.pytest.ini_options]
  DJANGO_SETTINGS_MODULE = "tests.test_settings"  # NOT config.settings — see tests/test_settings.py
  python_files = "test_*.py"
  testpaths = ["tests"]
  addopts = "-ra -q --strict-markers"
  ```

- Configure `ruff`:

  ```toml
  [tool.ruff]
  line-length = 100
  target-version = "py313"

  [tool.ruff.lint]
  select = ["E", "F", "I", "N", "W", "UP", "B", "A", "C4", "SIM"]
  ignore = ["E501"]
  ```

#### `.gitignore`

Must include at minimum:

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Env
.env
.env.local

# Django
*.log
db.sqlite3
staticfiles/
media/

# uv
# (uv.lock IS committed)

# Docker
.docker/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# gRPC generated stubs (Phase 7)
apps/grpc_service/generated/
```

`uv.lock` should NOT be in `.gitignore` — it must be committed.

#### `.dockerignore`

```
.git
.github
.venv
.env
.env.local
.pytest_cache
.ruff_cache
.mypy_cache
__pycache__
*.pyc
db.sqlite3
docker-compose*.yml
build_prompts
README.md
rag_system_guide.md
```

#### `.env.example`

All env vars used by `config/settings.py` must appear here with placeholder values. Use the following exact keys:

```bash
# ── Django ────────────────────────────────────────────────────────────
DJANGO_SECRET_KEY=change-me-generate-with-django-management
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,web
DJANGO_LOG_LEVEL=INFO

# ── Postgres ──────────────────────────────────────────────────────────
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=qdrant_rag
POSTGRES_USER=change-me
POSTGRES_PASSWORD=change-me

# ── Redis (Celery broker; unused in v1) ───────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Qdrant ────────────────────────────────────────────────────────────
QDRANT_HOST=qdrant
QDRANT_GRPC_PORT=6334
QDRANT_HTTP_PORT=6333
QDRANT_PREFER_GRPC=True
QDRANT_API_KEY=change-me-strong-secret

# ── Service ports ─────────────────────────────────────────────────────
HTTP_PORT=8000
GRPC_PORT=50051

# ── BGE-M3 (added in Phase 4 — leave the keys present so config is stable) ─
BGE_MODEL_NAME=BAAI/bge-m3
BGE_CACHE_DIR=/app/.cache/bge
BGE_USE_FP16=True
BGE_DEVICE=cpu
BGE_BATCH_SIZE=8

# ── Search defaults (Phase 7) ─────────────────────────────────────────
SEARCH_DEFAULT_TOP_K=5
SEARCH_MAX_TOP_K=20
SEARCH_THRESHOLD=0.65
SEARCH_PREFETCH_DENSE=50
SEARCH_PREFETCH_SPARSE=50
SEARCH_RRF_DENSE_WEIGHT=3.0
SEARCH_RRF_SPARSE_WEIGHT=1.0
```

> Note: do **not** include real credentials in `.env.example`. The user maintains `.env` locally with their actual values.

#### `manage.py`

Standard Django manage.py pointing at `config.settings`.

#### `config/__init__.py`

Must trigger Celery app load on Django startup:

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

#### `config/settings.py`

Key requirements:

- Read all config via `environ.Env()`. Call `env.read_env(BASE_DIR / ".env")` if file exists.
- `INSTALLED_APPS` must include:
  - `django.contrib.admin`, `auth`, `contenttypes`, `sessions`, `messages`, `staticfiles`
  - `rest_framework`
  - `apps.core`, `apps.tenants`, `apps.documents`, `apps.ingestion`, `apps.qdrant_core`, `apps.grpc_service`
- `DATABASES["default"]`: postgres via `psycopg`, host/port/user/password/name from env, with `CONN_MAX_AGE=60`.
- `REST_FRAMEWORK = {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"]}`
- `LOGGING`: minimal stdlib config that delegates to structlog (see `apps/core/logging.py`).
- `USE_TZ = True`, `TIME_ZONE = "UTC"`.
- Set `STATIC_URL = "/static/"`, `STATIC_ROOT = BASE_DIR / "staticfiles"` (admin needs this).
- A `QDRANT` dict containing `HOST`, `GRPC_PORT`, `HTTP_PORT`, `PREFER_GRPC`, `API_KEY` from env.
- A `BGE` dict (placeholder values from env; not used until Phase 4).
- A `SEARCH` dict (placeholder values from env; not used until Phase 7).
- `DEBUG` from env, default False.
- Configure `structlog` at import-time with `apps.core.logging.configure_logging()`.

#### `config/urls.py`

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
]
```

#### `config/wsgi.py` and `config/asgi.py`

Standard Django boilerplate pointing at `config.settings`.

#### `config/celery.py`

```python
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("qdrant_rag")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

In `settings.py`, set:

```python
CELERY_BROKER_URL = env("REDIS_URL")
CELERY_RESULT_BACKEND = env("REDIS_URL")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
```

The Celery worker container will start but have no tasks to run in v1. That is intentional.

#### `apps/core/apps.py`

```python
from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
```

(Same pattern for every other `apps/<name>/apps.py` — `name = "apps.<name>"`, `label = "<name>"`.)

#### `apps/core/logging.py`

structlog configuration. Must:

- Configure structlog with JSON output in production (`DEBUG=False`) and key-value console output in dev (`DEBUG=True`).
- Bind `service="qdrant_rag"` and `version="0.1.0-dev"` as default context.
- Provide a `configure_logging()` callable invoked from `settings.py`.
- Wire stdlib `logging` to delegate to structlog so Django/DRF/gunicorn logs flow through the same pipeline.
- Add a processor that copies any `request_id`, `tenant_id`, `bot_id`, `doc_id` fields from contextvars into log records (these will be set by future middleware in Phase 5/7; for now they're optional and absent).

#### `apps/core/urls.py`

```python
from django.urls import path
from apps.core.views import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
]
```

#### `apps/core/views.py`

Implement `healthz` as a Django view that:

1. Pings Postgres with a `SELECT 1` (use `django.db.connection.cursor()`).
2. Pings Qdrant via `qdrant_client.QdrantClient(...)` configured from settings, calling `client.get_collections()` (cheap).
3. Returns JSON shape:

   ```json
   {
     "status": "ok",
     "version": "0.1.0-dev",
     "components": {
       "postgres": "ok",
       "qdrant": "ok"
     }
   }
   ```

4. If any subsystem fails: status code **503**, the failing component reports `"error: <short message>"`, the others still reflect their actual state. Return the same JSON shape so monitoring can parse it consistently.
5. Wrap each ping in a **2-second wall-clock timeout** — `/healthz` must never hang the load balancer.

   **Implementation note:** Django's `connection.cursor()` and psycopg's `connect()` do **not** accept a per-call timeout argument under default settings, so a naive `cursor.execute("SELECT 1")` can hang for 30+ seconds when Postgres is unreachable. Enforce the 2-second budget with `concurrent.futures.ThreadPoolExecutor` (run the ping in a worker thread, await with `future.result(timeout=2.0)`, catch `TimeoutError` → report the subsystem as `"error: timeout"`). Apply the same pattern to the Qdrant ping. The `QdrantClient` itself supports `timeout=2.0` at construction; combine that with the executor wrapper for hard guarantee.

The Qdrant client used inside `healthz` should be cached at module level (lazy singleton) — connecting on every healthz call is wasteful. Use `prefer_grpc=True`, pass the API key, and gracefully handle connection errors (don't let them propagate).

#### `apps/tenants/apps.py`, `apps/documents/apps.py`, etc.

Each is just an `AppConfig` stub matching the pattern above. No models, no views, no urls. Phase 2+ will fill them in.

`apps/tenants/migrations/__init__.py` and `apps/documents/migrations/__init__.py` exist as empty files so Django recognizes the migration packages.

#### `proto/.gitkeep`

Empty file. Phase 7 will add `search.proto` here.

#### `scripts/compile_proto.sh`

```bash
#!/usr/bin/env bash
# Phase 7 will fill this in. For Phase 1 it's a no-op stub.
set -euo pipefail
echo "[compile_proto] No proto files yet. Phase 7 will populate this script."
```

Make it executable (`chmod +x`).

#### `scripts/verify_setup.py`

A standalone Python script (run via `uv run python scripts/verify_setup.py`) that:

1. Loads env (via `environ.Env()` reading `.env`).
2. Pings Postgres with `psycopg.connect()` — exits 1 if it fails.
3. Pings Qdrant via `qdrant_client.QdrantClient(...).get_collections()` — exits 1 if it fails.
4. Prints `[verify_setup] All checks passed.` on success, exits 0.

Used by humans during development to debug a broken stack. Not used by the running service.

Phase 4 will extend this to also verify BGE-M3 loads.

#### `tests/__init__.py`

Empty.

#### `tests/test_settings.py`

A test-only Django settings overlay so `pytest` can run from a fresh host shell without requiring the Compose stack to be up.

It does:
1. `from config.settings import *` — inherit everything.
2. Override `DATABASES["default"]` to in-memory SQLite (`"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"`). The healthz endpoint still pings the **real** configured Postgres at runtime via `psycopg`; this overlay only affects pytest-django's test database setup, which happens before any test code runs.
3. Append `"testserver"` to `ALLOWED_HOSTS` (Django's test client uses this as its host header; under `DEBUG=False` it would otherwise 400 before reaching the view).
4. Set `DEBUG=False` explicitly so test runs reflect production behavior.

#### `tests/conftest.py`

Set up `pytest-django` and ensure the test settings overlay is loaded:

```python
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")
```

Provide a fixture that returns a Django test client. No DB fixtures yet (Phase 2 owns models). Do **not** mark tests `@pytest.mark.django_db` unless they actually touch the ORM — Phase 1's healthz test does not.

Also add this in `pyproject.toml` so `pytest` picks up the overlay automatically:

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "tests.test_settings"
```

(Not `config.settings` — the overlay is the canonical settings module for test runs.)

#### `tests/test_healthz.py`

A single test. The healthz endpoint can be hit without the ORM — `pytest.mark.django_db` is **not** required and should be omitted:

```python
from django.test import Client


def test_healthz_returns_a_known_shape(client):
    """Smoke test: /healthz reachable and returns the documented shape."""
    # Accepts both 200 and 503 so the test passes whether or not Postgres/Qdrant
    # are reachable from the host. Integration tests (later phase) assert 200
    # specifically once the Compose stack is up.
    response = client.get("/healthz")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "status" in body
    assert "components" in body
    assert "postgres" in body["components"]
    assert "qdrant" in body["components"]
```

The reason both 200 and 503 are accepted: in unit-test mode without infrastructure the call may report 503; that's fine. Integration tests will assert 200 specifically once the Compose stack is up.

#### `Dockerfile`

Multi-stage, using uv from the official image:

```dockerfile
# syntax=docker/dockerfile:1.7

# ── Builder stage ─────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps without project (cache layer)
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# Copy app code and finalize install
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# ── Runtime stage ─────────────────────────────────────────────────────
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=builder /app /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000 50051

# Default command — overridden per-service in docker-compose.yml
CMD ["uv", "run", "gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
```

Notes for the agent:
- The `uv sync --frozen` line falls back to a non-frozen sync if `uv.lock` doesn't exist on first build. After first successful build, commit `uv.lock` to lock deps.
- Do **not** copy `.env` into the image. `.dockerignore` blocks it.
- **Do not** run migrations or collectstatic in the Dockerfile — those run via Compose `command:` overrides or one-shot containers later.

#### `docker-compose.yml`

Six services. Network: a single bridge `qdrant_rag_net`. Volumes: `postgres_data`, `redis_data`, `qdrant_data`, `bge_cache`.

```yaml
services:

  postgres:
    image: postgres:16-alpine
    container_name: qdrant_rag_postgres
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped
    networks: [qdrant_rag_net]

  redis:
    image: redis:7-alpine
    container_name: qdrant_rag_redis
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped
    networks: [qdrant_rag_net]

  qdrant:
    image: qdrant/qdrant:v1.17.1
    container_name: qdrant_rag_qdrant
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
      QDRANT__SERVICE__HTTP_PORT: 6333
      QDRANT__SERVICE__GRPC_PORT: 6334
      QDRANT__LOG_LEVEL: INFO
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD-SHELL", "bash -c ':> /dev/tcp/127.0.0.1/6333' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks: [qdrant_rag_net]

  web:
    build: .
    container_name: qdrant_rag_web
    command:
      - sh
      - -c
      - >-
          python manage.py migrate --noinput &&
          python manage.py collectstatic --noinput &&
          exec gunicorn config.wsgi:application
          --bind 0.0.0.0:8000
          --workers 2
          --timeout 90
          --access-logfile -
          --error-logfile -
    env_file: .env
    depends_on:
      postgres: {condition: service_healthy}
      redis:    {condition: service_healthy}
      qdrant:   {condition: service_healthy}
    ports:
      - "${HTTP_PORT:-8000}:8000"
    volumes:
      - bge_cache:/app/.cache/bge
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/healthz"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    networks: [qdrant_rag_net]

  grpc:
    build: .
    container_name: qdrant_rag_grpc
    # Phase 7 will replace this with the actual gRPC server.
    command: sh -c "echo 'gRPC service not implemented yet (Phase 7).' && sleep infinity"
    env_file: .env
    depends_on:
      web: {condition: service_healthy}
    ports:
      - "${GRPC_PORT:-50051}:50051"
    volumes:
      - bge_cache:/app/.cache/bge
    restart: unless-stopped
    networks: [qdrant_rag_net]

  worker:
    build: .
    container_name: qdrant_rag_worker
    command: celery -A config worker -l INFO --concurrency=2
    env_file: .env
    depends_on:
      postgres: {condition: service_healthy}
      redis:    {condition: service_healthy}
    restart: unless-stopped
    networks: [qdrant_rag_net]

volumes:
  postgres_data:
  redis_data:
  qdrant_data:
  bge_cache:

networks:
  qdrant_rag_net:
    driver: bridge
```

Pitfalls to avoid:
- The `web` healthcheck has `start_period: 30s` so initial migration time doesn't trigger false failures.
- The `grpc` service intentionally runs `sleep infinity` in Phase 1. This is correct.
- The `worker` service runs Celery but has zero registered tasks. It will idle. This is correct.
- Do **not** add `web: condition: service_healthy` to the `worker` dependency chain — the worker doesn't need the web container.

#### `docker-compose.override.yml`

Dev-only conveniences. Mount the source code for hot reload, expose Postgres on the host for direct connection, drop the `restart` policy:

```yaml
services:
  web:
    command: >
      sh -c "python manage.py migrate --noinput &&
             exec python manage.py runserver 0.0.0.0:8000"
    volumes:
      - .:/app
      - /app/.venv
      - bge_cache:/app/.cache/bge
    environment:
      DJANGO_DEBUG: "True"
    restart: "no"

  postgres:
    ports:
      - "5432:5432"
    restart: "no"

  redis:
    ports:
      - "6379:6379"
    restart: "no"

  worker:
    volumes:
      - .:/app
      - /app/.venv
    restart: "no"
```

The `- /app/.venv` lines on `web` and `worker` declare anonymous volumes that overlay the bind mount and shield the image's `/app/.venv` from the host's stale venv. Without them, every container start triggers a full runtime `uv sync` (see pitfall #6).

This file is automatically merged by Compose in dev. In prod-style runs use `docker compose -f docker-compose.yml up` (without override).

#### `.github/workflows/ci.yml`

A minimal CI workflow that:

1. Triggers on push and pull_request.
2. Sets up Python 3.13.
3. Installs uv.
4. Spins up Postgres + Qdrant via service containers.
5. Runs `uv sync`.
6. Runs `uv run ruff check .`
7. Runs `uv run pytest`.

Use the `astral-sh/setup-uv` action. Service container env should match what `settings.py` reads from `.env` — set them via `env:` block on the test job.

The CI must pass for Phase 1 with the single healthz test. If Postgres/Qdrant aren't actually up in CI, the test still passes because it accepts 503.

---

## Acceptance criteria

Phase 1 is complete when **all** of these pass on a clean checkout:

1. `uv sync` completes without errors. `uv.lock` is generated and committed.
2. `uv run ruff check .` reports zero violations.
3. `uv run pytest` runs and the single healthz test passes (regardless of whether infra is up — the test accepts both 200 and 503).
4. `docker compose build` completes without errors. The final web image is reasonable in size (under ~800 MB; FlagEmbedding is not yet installed so torch is absent).
5. `docker compose up -d` brings all six containers up. Within 60 seconds:
   - `docker compose ps` shows all containers as `healthy` (postgres, redis, qdrant, web) or `running` (grpc, worker).
   - `curl -fsS http://localhost:8000/healthz` returns HTTP 200 with body `{"status":"ok","version":"0.1.0-dev","components":{"postgres":"ok","qdrant":"ok"}}` (whitespace may differ).
6. `curl http://localhost:8000/admin/login/` returns HTTP 200 (Django admin login page).
7. Stopping Qdrant (`docker compose stop qdrant`) and re-hitting `/healthz` returns HTTP 503 with `qdrant` reporting an error string. Postgres still reports `ok`.
8. `docker compose logs web` shows JSON-formatted log lines (or key-value formatted if `DJANGO_DEBUG=True`).
9. `uv run python scripts/verify_setup.py` exits 0 when the stack is up; exits 1 when Postgres or Qdrant is down.
10. `docker compose down` cleanly stops everything. `docker compose down -v` also drops volumes without errors.

The user (or you, the agent) will execute these checks to verify completion.

---

## Common pitfalls

1. **Torch ends up in the image.** Phase 1 must not install torch. If `uv sync` ever pulls torch, the Phase 4 `pyproject.toml` change has been done prematurely. Verify with `uv pip list | grep -i torch` (should be empty) inside the container.

2. **`.env` accidentally committed.** Run `git status` before committing — only `.env.example` should be tracked.

3. **Postgres connection refused on first boot.** The web container starts before Postgres is fully ready despite `depends_on: service_healthy`. The healthcheck command in Compose (`pg_isready`) is generous; if you see flakes, increase `start_period` on the `web` healthcheck rather than weakening Postgres's check.

4. **Qdrant API key missing.** If `QDRANT_API_KEY` is empty, Qdrant rejects all requests including healthz pings. Ensure `.env` has a non-empty value and the value is passed to both the qdrant container (`QDRANT__SERVICE__API_KEY`) and the Python `QdrantClient(api_key=...)`.

5. **gRPC port 6334 vs 50051 confusion.** Qdrant's gRPC port is **6334** (used by the web service to talk to Qdrant). The qdrant_rag service's own gRPC server listens on **50051** (Phase 7). Do not conflate them.

6. **Bind-mounted source shadows the image's venv.** The dev override mounts the host project root at `/app` for hot reload. Without an additional escape, this hides the image's `/app/.venv` and the container falls back to the host venv (which has foreign Python interpreter paths) — `uv run` then re-runs `uv sync` at every container start, downloads every wheel from scratch, and the web healthcheck times out before gunicorn ever starts. **Required fix:** add an anonymous volume at `/app/.venv` for both the `web` and `worker` services in the override (`- /app/.venv` on its own line, immediately after `- .:/app`). The anonymous volume initialises from the image's venv on first container start and stays out of the host's way. **Trade-off:** after editing `pyproject.toml` or `uv.lock`, run `docker compose down -v && docker compose up -d --build` to drop the stale venv volume and recreate it from the rebuilt image. Plain `docker compose restart` (and `stop`/`start`) keeps the existing container and reuses the old anonymous volume — only container recreation (via `up --build` or `down`/`up`) replaces it. Prod-style runs (`docker compose -f docker-compose.yml up`) are unaffected because there is no bind mount to shadow the image's venv.

7. **structlog output is not JSON.** Verify `DJANGO_DEBUG=False` for prod-style runs. In dev mode (default of override file) you get console output, which is intentional.

8. **`apps/<name>/apps.py` config name is wrong.** The `name` attribute must be `apps.<name>` (the dotted path from project root), not just `<name>`. Otherwise `INSTALLED_APPS` resolution breaks.

9. **Missing `apps/__init__.py`.** Without it, `apps.core` etc. are not importable. Always present, even if empty.

10. **CI fails because Postgres service container has no DB.** The GitHub service container uses `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` env vars. Match what `settings.py` expects.

11. **`pytest` from a host shell can't reach the Compose-internal Postgres.** `config/settings.py` points `POSTGRES_HOST` at `postgres` (the Compose service name), which doesn't resolve outside the Compose network. Test runs from a fresh-checkout host shell would fail at DB setup. **Resolution:** `tests/test_settings.py` overlays `DATABASES["default"]` to in-memory SQLite for test runs only; `pyproject.toml`'s `[tool.pytest.ini_options].DJANGO_SETTINGS_MODULE = "tests.test_settings"` makes pytest pick it up automatically. The healthz endpoint still pings the **real** configured Postgres at runtime — the overlay only affects pytest-django's DB setup.

12. **Django test client gets 400 under `DEBUG=False`.** Django's test client uses `Host: testserver`; if `ALLOWED_HOSTS` doesn't include it, every test request returns 400 before reaching the view. **Resolution:** `tests/test_settings.py` appends `"testserver"` to `ALLOWED_HOSTS`. Do **not** add `testserver` to `.env.example`'s `DJANGO_ALLOWED_HOSTS` — that would leak a test-only host into production config.

13. **`/healthz` hangs longer than the 2-second budget.** `connection.cursor()` and psycopg's `connect()` ignore wall-clock timeouts under default settings; `cursor.execute("SELECT 1")` can hang for 30+ seconds when Postgres is unreachable. **Resolution:** wrap each subsystem ping in `concurrent.futures.ThreadPoolExecutor` and call `future.result(timeout=2.0)`, catching `TimeoutError` to report the subsystem as `"error: timeout"`. The same pattern applies to the Qdrant ping (Qdrant client's own `timeout=2.0` parameter is necessary but not sufficient — the executor wrapper guarantees the budget).

14a. **YAML folded scalar (`command: > ... sh -c "..."`) silently splits multi-line shell commands and drops flags after `exec`.** When `command:` is a `>`-folded scalar containing a multi-line `sh -c "..."` string, YAML preserves more-indented continuation lines as a *separate paragraph* with a literal newline between paragraphs. The shell then parses each paragraph as a separate statement. With a script like `... && exec gunicorn config.wsgi:application\n    --bind 0.0.0.0:8000 ...`, the shell runs `exec gunicorn config.wsgi:application` (no flags) FIRST — which replaces the shell with gunicorn. The flags on the next line are never reached. Gunicorn defaults to `127.0.0.1:8000`, the Docker port forward goes to `eth0:8000` where nothing listens, and host curl gets "Connection reset by peer" while the container's loopback healthcheck still passes. **Resolution:** use **YAML list form** for `command:`, with `>-` (strip-folded) and *uniform indentation* on all content lines so they fold into a single shell line:

```yaml
command:
  - sh
  - -c
  - >-
      python manage.py migrate --noinput &&
      python manage.py collectstatic --noinput &&
      exec gunicorn config.wsgi:application
      --bind 0.0.0.0:8000
      --workers 2
      --timeout 90
      --access-logfile -
      --error-logfile -
```

Verify after rebuild with `docker compose exec web sh -c 'cat /proc/1/cmdline | tr "\0" " "'` — must show all flags (not just `gunicorn config.wsgi:application`). Same trap applies to any compose service whose `command:` builds a multi-line shell pipeline ending with `exec <binary>`.

14. **`uv run` triggers a re-sync at runtime when image was built with `--no-dev`.** The Dockerfile builds the venv with `uv sync --no-dev` (correct for production: dev tools like ruff/mypy/pytest don't belong in the prod image). But `uv run` **defaults to including dev deps** — at runtime it sees the venv is "missing" dev deps and tries to install them on every container start, downloading `pygments`, `ruff`, `mypy`, etc. on the fly. The healthcheck deadline (5 retries × 15s + 30s start_period ≈ 105s) expires before the sync finishes → web marked unhealthy. **Resolution:** in `docker-compose.yml`'s production-mode services (`web` and `worker`), call binaries directly instead of via `uv run`. The Dockerfile already puts `/app/.venv/bin` on `PATH`, so `gunicorn`, `python`, `celery` resolve to the image's pinned binaries — no auto-sync, deterministic, fast startup. Use `exec gunicorn ...` so the shell is replaced and signals propagate. The dev override (`docker-compose.override.yml`) keeps `uv run` because dev wants dev deps and auto-sync on lockfile drift.

---

## Out of scope for Phase 1 (explicit)

Do **not** implement these in Phase 1. They belong to later phases:

- Tenant / Bot / Document Django models — Phase 2
- `collection_name()` helper — Phase 2
- Slug regex validators — Phase 2
- Qdrant client wrapper / collection factory — Phase 3
- BGE-M3 embedder, FlagEmbedding, torch — Phase 4
- `RecursiveCharacterTextSplitter` chunker — Phase 4
- DRF serializers for the upload payload — Phase 5
- POST `/v1/.../documents` route — Phase 5
- DELETE `/v1/.../documents/<doc_id>` route — Phase 6
- gRPC `.proto` file, server, search RPC — Phase 7
- Prometheus metrics — Phase 8
- Audit log — deferred to v3
- Authentication — deferred (URL structure leaves room for it)

If you find yourself writing any of the above, stop and re-read this section.

---

## When you finish

1. Confirm all acceptance criteria pass.
2. Commit `uv.lock` along with the rest. Do **not** commit `.env`.
3. Output a short report:
   - Files created (count + paths)
   - Acceptance criteria results (✓/✗ per criterion)
   - Any deviations from this prompt and why
   - Anything ambiguous that the user should confirm before Phase 2

That's Phase 1. Phase 2 (Domain Models) builds on top of this.
