# Phase 8b — Step 3 of 3: Implement & Verify

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Now you write code. Follow the revised plan.**

---

## Required reading (in this order)

1. `build_prompts/phase_8b_ops_deployment/spec.md` — non-negotiable.
2. `build_prompts/phase_8b_ops_deployment/plan.md` — revised plan after Step 2 review.
3. `build_prompts/phase_8b_ops_deployment/plan_review.md` — review findings (especially [critical] / [major]).
4. `build_prompts/phase_8a_code_hardening/implementation_report.md` — 8a outcomes (the three deviations Section 0 closes).
5. `apps/core/middleware.py`, `apps/core/logging.py`, `apps/core/metrics.py`, `apps/grpc_service/handler.py`, `apps/ingestion/{embedder,pipeline}.py`, `apps/qdrant_core/search.py`, `Makefile`, `docker-compose.yml`, `.env.example` — current state.

If any of these don't exist, abort.

---

## Your task

Implement Phase 8b per the revised plan. **8 new files + 8 modified + 1 test update.**

### Section 0 — 8a polish (do FIRST)
1. `apps/core/logging.py` — MODIFIED: add `structlog.stdlib.ExtraAdder()` to both processor chains
2. `apps/core/middleware.py` — MODIFIED: narrow RequestID exclusion to `/static/` only; add counter recorder calls in AccessLogMiddleware
3. `apps/core/metrics_recorders.py` — NEW: three recorder helpers
4. `apps/grpc_service/handler.py` — MODIFIED: `_record_metrics` decorator on Search + HealthCheck
5. `apps/ingestion/embedder.py` — MODIFIED: `embedder_loaded.set(1)` after model load
6. `apps/qdrant_core/search.py` — MODIFIED: `search_threshold_used` gauge + `search_results_count` histogram on success
7. `tests/test_observability.py` — UPDATED: assertions check for `method`/`path`/`status_code`/`duration_ms`/`phases`

### Section 1 — Operational artifacts
8. `docker-compose.yml` — MODIFIED: `stop_grace_period: 30s` on `grpc` service
9. `Makefile` — MODIFIED: `snapshot`, `backup`, `load-test` targets + help update
10. `.env.example` — MODIFIED: document new env vars
11. `scripts/snapshot_qdrant.sh` — NEW
12. `scripts/backup_postgres.sh` — NEW
13. `scripts/bootstrap.sh` — NEW
14. `deploy/qdrant-rag.service` — NEW (systemd unit)
15. `deploy/nginx/qdrant_rag.conf.example` — NEW
16. `RUNBOOK.md` — NEW
17. `.github/workflows/ci.yml` — NEW
18. `README.md` — MODIFIED (mark Phase 8b complete; add Deployment section)

---

## Hard rules

1. Implement only what the plan says. No drive-by refactors.
2. Do NOT modify any file outside the modification list.
3. **No new Python dependencies.** Recorder helpers use `prometheus-client` already installed.
4. **No new Postgres dep on host.** `backup_postgres.sh` invokes via `docker compose exec postgres pg_dump`.
5. **bootstrap.sh runs as root.** Documented at top: `sudo bash scripts/bootstrap.sh`.
6. **bootstrap.sh is idempotent.** Re-runs on already-bootstrapped hosts exit 0 cleanly.
7. **Section 0 changes ship FIRST** so verification of Section 1 sees corrected behavior.
8. **`url_name`-based endpoint label** for HTTP counter (NOT `request.path`).
9. **gRPC decorator captures status code at handler exit** (default OK, exception → mapped code).
10. **CI runs against the SQLite test_settings overlay** (matching local `make test`); NO Postgres + Qdrant service containers.
11. **`make rebuild`** (not `make wipe`) — preserve volumes.
12. **Phase 1-8a regression: full suite stays green.**

---

## Step-by-step

### Section 0 — 8a polish

#### Step 1 — `apps/core/logging.py`

Add `structlog.stdlib.ExtraAdder()` to both processor chains. Place AFTER `add_log_level` and BEFORE `_request_context_processor` (which is a custom processor in the existing config).

Verify: `make run python -c "import structlog.stdlib; from apps.core.logging import _SHARED_PROCESSORS; assert any(isinstance(p, structlog.stdlib.ExtraAdder) for p in _SHARED_PROCESSORS)"` succeeds.

#### Step 2 — `apps/core/metrics_recorders.py`

```python
"""Metric-recording helpers; called from middleware/handler/embedder/search."""

from __future__ import annotations

from apps.core.metrics import (
    grpc_request_duration_seconds,
    grpc_requests_total,
    http_request_duration_seconds,
    http_requests_total,
    pipeline_phase_duration_seconds,
)


def record_http_request(method: str, endpoint: str, status_code: int, duration_seconds: float) -> None:
    http_requests_total.labels(method=method, endpoint=endpoint, status_code=str(status_code)).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration_seconds)


def record_grpc_request(rpc: str, status_code: str, duration_seconds: float) -> None:
    grpc_requests_total.labels(rpc=rpc, status_code=status_code).inc()
    grpc_request_duration_seconds.labels(rpc=rpc).observe(duration_seconds)


def record_pipeline_phase(phase: str, duration_seconds: float) -> None:
    pipeline_phase_duration_seconds.labels(phase=phase).observe(duration_seconds)
```

#### Step 3 — `apps/core/middleware.py`

Two changes:
1. Change `_REQUESTID_EXCLUDED_PATHS` to `("/static/",)` only (narrow from previous over-exclusion). Keep `_ACCESSLOG_EXCLUDED_PATHS` as before.
2. After the access log emit (in the finally block), call recorder helpers:
   ```python
   from apps.core.metrics_recorders import record_http_request, record_pipeline_phase

   url_name = (request.resolver_match.url_name if request.resolver_match else "unknown") or "unknown"
   record_http_request(
       method=request.method,
       endpoint=url_name,
       status_code=status_code,
       duration_seconds=duration_ms / 1000.0,
   )
   for phase, ms in (phases or {}).items():
       record_pipeline_phase(phase=phase, duration_seconds=ms / 1000.0)
   ```

Verify: `make run python manage.py check` clean.

#### Step 4 — `apps/grpc_service/handler.py`

Add a `_record_metrics(rpc_name)` decorator. Wraps each method:

```python
import functools
import time
import grpc
from apps.core.metrics_recorders import record_grpc_request


def _record_metrics(rpc_name: str):
    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, request, context):
            started = time.monotonic()
            status_code_name = "OK"
            try:
                response = method(self, request, context)
                # Capture status if handler set one
                code = context.code()
                if code is not None and code != grpc.StatusCode.OK:
                    status_code_name = code.name
                return response
            except grpc.RpcError as exc:
                status_code_name = (exc.code().name if exc.code() else "UNKNOWN")
                raise
            except Exception:
                status_code_name = "INTERNAL"
                raise
            finally:
                record_grpc_request(
                    rpc=rpc_name,
                    status_code=status_code_name,
                    duration_seconds=time.monotonic() - started,
                )
        return wrapper
    return decorator
```

Apply: `@_record_metrics("Search")` on `Search`; `@_record_metrics("HealthCheck")` on `HealthCheck`.

#### Step 5 — `apps/ingestion/embedder.py`

In `_get_model()`, after the model load completes successfully, call `embedder_loaded.set(1)`. Single line addition.

#### Step 6 — `apps/qdrant_core/search.py`

In the function that returns `{"chunks": ..., "total_candidates": N, "threshold_used": T}`, before returning:

```python
from apps.core.metrics import search_results_count, search_threshold_used

search_results_count.observe(total_candidates)
search_threshold_used.set(threshold_used)
```

#### Step 7 — `tests/test_observability.py`

Replace any assertions that previously didn't check for `method` / `path` / `status_code` / `duration_ms` / `phases` with positive checks. Specifically, in the `test_request_completed_log_emitted` test:
```python
log = json.loads(captured_line)
assert log["event"] == "request_completed"
assert "request_id" in log
assert log["method"] == "POST"
assert log["path"].startswith("/v1/")
assert log["status_code"] in (200, 201)
assert isinstance(log["duration_ms"], (int, float))
assert isinstance(log.get("phases"), dict)
```

Verify: `make run pytest tests/test_observability.py -v` all green.

#### Step 8 — Stack rebuild + Section 0 verification

```bash
make rebuild
make ps && make health
curl -i http://localhost:8080/healthz | grep X-Request-ID    # present
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/search \
  -H "Content-Type: application/json" -d '{"query":"smoke"}' >/dev/null
curl -sS http://localhost:8080/metrics | grep -E 'http_requests_total\{|search_results_count_count|embedder_loaded'
make logs 2>&1 | grep request_completed | tail -1            # should now have method/path/status_code/duration_ms/phases
```

### Section 1 — Operational artifacts

#### Step 9 — `docker-compose.yml`

Add to `grpc` service: `stop_grace_period: 30s`.

#### Step 10 — `.env.example`

Document new env vars at the bottom:
```
# Backups
BACKUP_DIR_QDRANT=/var/backups/qdrant
BACKUP_DIR_POSTGRES=/var/backups/postgres
QDRANT_SNAPSHOT_KEEP=7
POSTGRES_BACKUP_KEEP=14

# Bootstrap
DEPLOY_USER=  # default: $SUDO_USER
```

#### Step 11 — `Makefile`

Add to `.PHONY`: `snapshot`, `backup`, `load-test`. Add help block lines. Add target rules:
```makefile
snapshot:
	bash scripts/snapshot_qdrant.sh

backup:
	bash scripts/backup_postgres.sh

load-test:
	@if ! curl -fsS http://localhost:$(WEB_PORT)/healthz >/dev/null; then \
		echo "Stack not up — run 'make up' first."; exit 1; \
	fi
	python scripts/load_test.py
```

#### Step 12 — `scripts/snapshot_qdrant.sh`

Bash script:
- `set -euo pipefail`
- Load `.env` (set -a; source .env; set +a)
- Resolve `${BACKUP_DIR_QDRANT:=/var/backups/qdrant}`, `${QDRANT_SNAPSHOT_KEEP:=7}`
- Optional CLI arg: collection name (else, all collections via `GET /collections`)
- `mkdir -p $BACKUP_DIR_QDRANT/<timestamp>/`
- For each collection: `curl -X POST http://localhost:6333/collections/<name>/snapshots` → store snapshot file
- Rotation: keep last N snapshot dirs by mtime; remove older
- Trap on error: remove the partial snapshot dir; exit non-zero
- Exit 0 with summary on success

#### Step 13 — `scripts/backup_postgres.sh`

Bash script:
- `set -euo pipefail`
- Load `.env`
- Resolve `${BACKUP_DIR_POSTGRES:=/var/backups/postgres}`, `${POSTGRES_BACKUP_KEEP:=14}`
- `mkdir -p $BACKUP_DIR_POSTGRES`
- Filename: `qdrant_rag_$(date +%Y%m%dT%H%M%S).pgdump`
- `docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > $BACKUP_DIR_POSTGRES/<filename>`
- Verify file is non-empty
- Rotation: keep last N files by mtime
- Exit 0 with summary on success

#### Step 14 — `scripts/bootstrap.sh`

Bash script:
- Header comment: "Run as root: sudo bash scripts/bootstrap.sh"
- `set -euo pipefail`
- Tee output to `/var/log/qdrant_rag_bootstrap.log`
- Preflight: check root (`[[ $EUID -eq 0 ]]`); check `command -v docker`; check `docker compose version`; check `systemctl is-active docker`
- `DEPLOY_USER=${DEPLOY_USER:-$SUDO_USER}` — fail clearly if both unset
- Idempotency check: if `docker compose ps -q web 2>/dev/null` returns non-empty AND `make health` succeeds → "already bootstrapped; nothing to do" → exit 0
- Add `$DEPLOY_USER` to `docker` group if not already
- Copy `.env.example` to `.env` if `.env` missing; warn operator to edit secrets
- As `$DEPLOY_USER` (via `sg docker -c`):
  - `make bge-download` (~5-15 min)
  - `make up`
  - poll `make health` for up to 180s
- On health success: print "DONE. Stack live on http://localhost:${HTTP_PORT:-8080}"
- On health failure within timeout: print "see logs with: make logs"; exit 1

#### Step 15 — `deploy/qdrant-rag.service`

Systemd unit:
```ini
[Unit]
Description=qdrant_rag service stack
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=true
WorkingDirectory=/path/to/qdrant_rag
User=DEPLOY_USER_PLACEHOLDER
ExecStart=/usr/bin/make up
ExecStop=/usr/bin/make down
TimeoutStartSec=300
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Document at top: replace `WorkingDirectory` and `User` per host. Verify with `systemd-analyze verify deploy/qdrant-rag.service` (after substitution).

#### Step 16 — `deploy/nginx/qdrant_rag.conf.example`

nginx config:
```nginx
# Replace placeholders before deploying:
#   qdrant.your-domain.com   → your HTTP API hostname
#   grpc.your-domain.com     → your gRPC hostname
#   /etc/letsencrypt/live/.../fullchain.pem
#   /etc/letsencrypt/live/.../privkey.pem
#   /metrics IP allowlist     → your internal CIDRs

# HTTP API
server {
    listen 80;
    server_name qdrant.your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name qdrant.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/qdrant.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/qdrant.your-domain.com/privkey.pem;

    client_max_body_size 50m;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;

    location /metrics {
        allow 10.0.0.0/8;
        allow 192.168.0.0/16;
        deny all;
        proxy_pass http://127.0.0.1:8080;
    }

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}

# gRPC
server {
    listen 443 ssl http2;
    server_name grpc.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/grpc.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/grpc.your-domain.com/privkey.pem;

    grpc_read_timeout 120s;
    grpc_send_timeout 120s;

    location / {
        grpc_pass grpc://127.0.0.1:50051;
        error_page 502 = /error502grpc;
    }

    location = /error502grpc {
        internal;
        default_type application/grpc;
        add_header grpc-status 14;
        add_header grpc-message "upstream unavailable";
        return 204;
    }
}
```

Verify: `nginx -t -c <substituted-conf>` syntax check OK.

#### Step 17 — `RUNBOOK.md`

10 sections in this exact order:

1. **Deploy from scratch** — `bash scripts/bootstrap.sh` walkthrough
2. **Upgrade** — `git pull && make rebuild && make ps && make health`
3. **Rollback** — `git checkout <prior-commit> && make rebuild`
4. **Restart a single service** — `docker compose restart web`
5. **Read logs** — `make logs`, filter by `request_completed`, search by `request_id`
6. **Read metrics** — `curl /metrics`, key metric families, recommended scrape interval
7. **Restore Postgres** — `docker compose exec -T postgres pg_restore -U $POSTGRES_USER -d $POSTGRES_DB < backup.pgdump`
8. **Restore Qdrant** — collection-by-collection snapshot restore via Qdrant HTTP API
9. **Rotate secrets** — per-secret recipe (POSTGRES_PASSWORD, QDRANT_API_KEY, DJANGO_SECRET_KEY)
10. **Common failure modes** — BGE cache corrupted, Postgres connection limit, Qdrant disk full, gRPC port collision, healthz timing out

Each section ends with "verify success by:" + 1-3 commands and expected output snippets.

#### Step 18 — `.github/workflows/ci.yml`

Single workflow:
- name: "ci"
- triggers: `pull_request`, `push: branches: [main]`
- single job: `test`
- runs-on: ubuntu-latest
- steps:
  1. checkout
  2. set up python 3.13 (`actions/setup-python@v5`)
  3. install uv (`astral-sh/setup-uv@v3`)
  4. cache uv lock (`actions/cache@v4` keyed on `uv.lock`)
  5. `uv sync --frozen --all-groups` (includes dev deps)
  6. `uv run ruff check .`
  7. `uv run ruff format --check .`
  8. `uv run python manage.py makemigrations --check --dry-run`
  9. `uv run pytest -v` (tests use SQLite overlay via `tests.test_settings`; embedder host-blocked tests skip; the 1 known `test_500_envelope` failure stays; everything else green)

Expected runtime: ~3-5 min.

#### Step 19 — `README.md`

- Status table: mark Phase 8b COMPLETE.
- Add new "Deployment" section near the top after Quick Start, linking to `RUNBOOK.md` and `scripts/bootstrap.sh`.

#### Step 20 — Final regression

```bash
make rebuild
make ps && make health
make run pytest -v
make snapshot && make backup
bash scripts/bootstrap.sh   # idempotent on already-bootstrapped host → exit 0 with skip message
```

All green.

---

## Implementation report

Write `build_prompts/phase_8b_ops_deployment/implementation_report.md`:

1. Status: PASS / PARTIAL / FAIL.
2. Files modified/created with line counts.
3. Tests count + pass/fail (with delta vs Phase 8a's 164).
4. All 18 acceptance criteria with PASS/FAIL/SKIP.
5. Manual smoke output:
   - `/healthz` X-Request-ID present
   - `/metrics` non-zero counters
   - Access log line with all kwargs
   - `make snapshot` and `make backup` succeed
   - `bash scripts/bootstrap.sh` idempotent
   - `systemd-analyze verify` clean
   - `nginx -t` clean
6. Phase 1-8a regression status.
7. `make ps` snapshot.
8. CI workflow trigger result (if pushed).
9. Notable deviations + justification.
10. **v1 SHIP confirmation.**

---

## What "done" looks like

Output to chat:

1. All Section 0 + Section 1 file changes applied.
2. PASS/FAIL on each acceptance criterion.
3. Manual smoke confirmations.
4. Test count delta.
5. **"v1 ships."**

Then **stop**.
