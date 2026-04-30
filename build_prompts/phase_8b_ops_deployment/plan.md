# Phase 8b — Implementation Plan (revised)

> Produced by Prompt 1 (PLAN), revised by Prompt 2 (REVIEW). Inputs: Phase 8b spec.md, Phase 8a outcomes (closes 3 deviations), current state of source files, plan_review.md.

---

## 0. Revision notes

This plan is revision 2. Findings from `plan_review.md`:

- **F1 [major]:** Counter wiring placement — already in plan §3.3 + R2. Confirmed in implementation order.
- **F2 [major]:** Phase timer ms→seconds conversion — already in plan §3.3 + R4. `/1000.0` explicit.
- **F3 [major]:** `/metrics` X-Request-ID assertion flip — already in plan §3.7 + A2. Contract change documented.
- **F4 [major]:** bootstrap.sh first-run-without-secrets — already in plan §3.11 + R5. `exit 3` on first run.
- **F5 [major]:** gRPC decorator status code capture — already in plan §3.4 + R12. Three branches (return/RpcError/Exception).
- **F10 [minor]:** Add bootstrap.sh `tee /var/log/qdrant_rag_bootstrap.log` for audit. §3.11 revised inline.
- **F6-F16 minors** documented inline.

Zero critical findings. Plan proceeds.

---

## 1. Plan summary

Phase 8b is the **final phase** of the v1 build, packing two scope sections into one ship: **Section 0** closes the three documented deviations from Phase 8a (narrow `RequestIDMiddleware` exclusion, add `structlog.stdlib.ExtraAdder` to both processor chains, wire counter increments into recording sites) so live traffic actually populates `/metrics` and the access log carries the documented 5-key shape; **Section 1** delivers the operational shell — `bootstrap.sh` for fresh-VM onboarding (idempotent, root-launched, BGE-M3 download, `sg docker -c` to skip the relogin ritual), `snapshot_qdrant.sh` + `backup_postgres.sh` with built-in rotation, an optional systemd unit + nginx HTTP/gRPC reverse-proxy template, a 10-section RUNBOOK, GitHub Actions CI workflow with `uv.lock` cache, plus three new Makefile targets and a one-line Compose `stop_grace_period: 30s` tweak. The riskiest pieces are: (a) **counter recorder placement** — `record_http_request` MUST be called from inside `AccessLogMiddleware`'s finally block AFTER the path-exclusion check, otherwise either every `/metrics` scrape adds counter ticks (self-feedback) or non-excluded requests don't get recorded; (b) **`ExtraAdder` ordering** in the structlog processor chain — must come AFTER `add_log_level` and BEFORE `_request_context_processor`, else either KeyError or dropped fields; (c) **`bootstrap.sh` idempotency** — re-running on an already-bootstrapped host MUST NOT re-add to docker group (skip if already member), MUST NOT overwrite an existing `.env`, MUST NOT re-download BGE-M3 if `.bge_cache/` is populated, and MUST exit clean when stack is already up; (d) **phase timer unit conversion** — Phase 8a's `timer(...)` records milliseconds; Prometheus histograms expect seconds, so the recorder dispatch divides by 1000.0. The build verifies itself via: import smokes after Section 0 each step, `manage.py check`, focused `tests/test_observability.py` re-run (asserting NEW positive shape, not absence), `make rebuild` then `curl /metrics | grep _total` showing non-zero counters, `make logs | grep request_completed` showing 5-key access log, `bash scripts/snapshot_qdrant.sh` + `bash scripts/backup_postgres.sh` exit 0 + create rotated artifacts, idempotent re-run of `bootstrap.sh`, `systemd-analyze verify` on the unit, `nginx -t` on the config, full Phase 1-8a regression.

---

## 2. Build order & dependency graph

| # | Section | Artifact | Depends on | Why |
|---|---|---|---|---|
| 1 | 0 | `apps/core/logging.py` | — | Add `structlog.stdlib.ExtraAdder()` to both processor chains. |
| 2 | 0 | `apps/core/metrics_recorders.py` | apps/core/metrics.py | NEW: 3 thin recorder helpers (record_http_request, record_grpc_request, record_pipeline_phase). |
| 3 | 0 | `apps/core/middleware.py` | 2 | Narrow `_REQUESTID_EXCLUDED_PATHS` to `("/static/",)`; integrate counter recorder calls in `AccessLogMiddleware.__call__` finally block (after access log emit). |
| 4 | 0 | `apps/grpc_service/handler.py` | 2 | Add `_record_metrics` decorator; wrap `Search` and `HealthCheck`. |
| 5 | 0 | `apps/ingestion/embedder.py` | apps/core/metrics.py | `embedder_loaded.set(1)` post-load in `_get_model()`. |
| 6 | 0 | `apps/qdrant_core/search.py` | apps/core/metrics.py | Set `search_threshold_used` gauge + observe `search_results_count` histogram on every successful search. |
| 7 | 0 | `tests/test_observability.py` | 1, 3 | Update assertions to check for the NEW positive shape (5 access-log keys, ExtraAdder behavior, counter increments). |
| 8 | 0 | Stack rebuild (host-equivalent) | 1-7 | Verify `/metrics` shows non-zero counters; `make logs | grep request_completed` shows 5-key shape. |
| 9 | 1 | `scripts/snapshot_qdrant.sh` | — | NEW (independent of any source change). |
| 10 | 1 | `scripts/backup_postgres.sh` | — | NEW (independent). |
| 11 | 1 | `scripts/bootstrap.sh` | — | NEW (independent). |
| 12 | 1 | `deploy/qdrant-rag.service` | — | NEW (systemd unit). |
| 13 | 1 | `deploy/nginx/qdrant_rag.conf.example` | — | NEW (template). |
| 14 | 1 | `Makefile` | 9, 10 | MODIFIED: add `snapshot`, `backup`, `load-test` targets + help block. |
| 15 | 1 | `docker-compose.yml` | — | MODIFIED: `stop_grace_period: 30s` on `grpc` service. |
| 16 | 1 | `.env.example` | — | MODIFIED: add `BACKUP_DIR_QDRANT`, `BACKUP_DIR_POSTGRES`, `QDRANT_SNAPSHOT_KEEP`, `POSTGRES_BACKUP_KEEP`, `DEPLOY_USER`. |
| 17 | 1 | `RUNBOOK.md` | 9-13, 14, 15 | NEW: 10 sections. Last-mile content; commands MUST reflect what works. |
| 18 | 1 | `.github/workflows/ci.yml` | — | NEW: GitHub Actions CI. |
| 19 | 1 | `README.md` | 1-18 | MODIFIED: mark Phase 8b complete; add Deployment section pointing to RUNBOOK + bootstrap.sh. |
| 20 | 1 | Final regression | 1-19 | Full Phase 1-8a host suite stays green. |

Notes:
- **Section 0 BEFORE Section 1** — spec hard constraint #1. The ops scripts (load test, RUNBOOK commands) reference live `/metrics` + access log; they must show the corrected behavior.
- Steps 9-13 (scripts + systemd + nginx) are dependency-independent; can be authored in any order. Sequenced for natural review flow.
- **RUNBOOK.md AFTER scripts (step 17)** — RUNBOOK commands quote the scripts; writing it before the scripts ship would be aspirational, not operationally useful (spec hard constraint #22).
- **Makefile AFTER scripts (step 14)** — `make snapshot/backup/load-test` targets shell-out to scripts; targets must be usable when added.
- **CI workflow LAST among Section 1 (step 18)** — ensures all upstream changes (rules, ruff, tests, scripts) are stable.
- **README.md last (step 19)** — final touch documenting the completed phase.
- Step 20 (final regression) confirms the full pipeline post-everything-shipped.

---

## 3. Build steps (sequenced)

### Step 3.1 — `apps/core/logging.py` add `ExtraAdder` to both chains

- **Goal:** stdlib `logger.info(..., extra={...})` kwargs render in JSON output.
- **Diff:** insert `structlog.stdlib.ExtraAdder()` AFTER `add_log_level` and BEFORE `_request_context_processor` in BOTH:
  - `_SHARED_PROCESSORS` (foreign_pre_chain via `ProcessorFormatter`).
  - The `structlog.configure(processors=[...])` chain.
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.core.logging import _SHARED_PROCESSORS
  names = [type(p).__name__ for p in _SHARED_PROCESSORS]
  print(names)
  assert 'ExtraAdder' in names
  "
  ```
- **Rollback:** revert.
- **Effort:** 10 min.

### Step 3.2 — `apps/core/metrics_recorders.py` (NEW)

- **Goal:** 3 thin recorder helpers — single import surface for middleware/handler/embedder/search.
- **Content (~50 lines):**
  ```python
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
- **Why a separate module:** `apps/core/metrics.py` (Phase 8a) defines the singletons; `metrics_recorders.py` is the call surface. Keeps imports trivial and avoids circular deps when middleware/handler import recorders.
- **Verification:** `uv run python -c "from apps.core.metrics_recorders import record_http_request, record_grpc_request, record_pipeline_phase; print('ok')"`.
- **Effort:** 10 min.

### Step 3.3 — `apps/core/middleware.py` narrow exclusion + counter wiring

- **Goal:** RequestIDMiddleware excludes ONLY `/static/`; AccessLogMiddleware emits counter increments after the access log emit.
- **Diff:**
  - Rename `_EXCLUDED_PREFIXES` → `_ACCESSLOG_EXCLUDED_PATHS = ("/metrics", "/healthz", "/static/", "/admin/")`.
  - Add `_REQUESTID_EXCLUDED_PATHS = ("/static/",)`.
  - `RequestIDMiddleware.__call__` checks `_REQUESTID_EXCLUDED_PATHS` (just `/static/`).
  - `AccessLogMiddleware.__call__` checks `_ACCESSLOG_EXCLUDED_PATHS` (4 paths).
  - In `AccessLogMiddleware.__call__` finally block, AFTER `logger.info("request_completed", extra=extra)`:
    ```python
    endpoint = (
        getattr(request.resolver_match, "url_name", None) or "unknown"
    ) if hasattr(request, "resolver_match") and request.resolver_match else "unknown"
    duration_seconds = duration_ms / 1000.0
    record_http_request(
        method=request.method,
        endpoint=endpoint,
        status_code=status_code,
        duration_seconds=duration_seconds,
    )
    for phase, ms in phases.items():
        record_pipeline_phase(phase=phase, duration_seconds=ms / 1000.0)
    ```
- **Why endpoint via `resolver_match.url_name`:** spec pitfall #15. `request.path` includes UUIDs → blows label cardinality. URL pattern names are bounded.
- **Why `/healthz` keeps RequestID:** spec hard constraint #2. Correlation IDs help when probes fail. `/healthz` STILL excluded from access log (no log spam).
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.core.middleware import _REQUESTID_EXCLUDED_PATHS, _ACCESSLOG_EXCLUDED_PATHS
  assert _REQUESTID_EXCLUDED_PATHS == ('/static/',)
  assert _ACCESSLOG_EXCLUDED_PATHS == ('/metrics', '/healthz', '/static/', '/admin/')
  print('exclusion lists ok')
  "
  uv run python manage.py check
  ```
- **Effort:** 25 min.

### Step 3.4 — `apps/grpc_service/handler.py` `_record_metrics` decorator

- **Goal:** wrap `Search` and `HealthCheck` to record gRPC counters + latency.
- **Diff:**
  ```python
  import time
  from functools import wraps
  import grpc
  from apps.core.metrics_recorders import record_grpc_request

  def _record_metrics(rpc_name: str):
      def deco(fn):
          @wraps(fn)
          def wrapper(self, request, context):
              started = time.monotonic()
              status_code = grpc.StatusCode.OK
              try:
                  result = fn(self, request, context)
                  # If handler called context.abort, an RpcError was raised already.
                  # context.code() returns None until set; use OK as default.
                  code = context.code() if context.code() is not None else grpc.StatusCode.OK
                  status_code = code
                  return result
              except grpc.RpcError as exc:
                  status_code = getattr(exc, "code", lambda: grpc.StatusCode.UNKNOWN)() or grpc.StatusCode.UNKNOWN
                  raise
              except Exception:
                  status_code = grpc.StatusCode.INTERNAL
                  raise
              finally:
                  record_grpc_request(
                      rpc=rpc_name,
                      status_code=status_code.name if hasattr(status_code, "name") else str(status_code),
                      duration_seconds=time.monotonic() - started,
                  )
          return wrapper
      return deco
  ```
  Then `@_record_metrics("Search")` on `Search`, `@_record_metrics("HealthCheck")` on `HealthCheck`.
- **CAUTION:** `context.abort(code, msg)` raises `grpc.RpcError` synchronously. The handler's existing flow uses `context.abort(...)` — the decorator's RpcError branch handles it. Default branch (OK) covers the normal-return path.
- **Verification:** import smoke + handler tests still green.
- **Effort:** 25 min.

### Step 3.5 — `apps/ingestion/embedder.py` `embedder_loaded.set(1)` post-load

- **Goal:** flip the gauge to 1 after BGE-M3 finishes loading.
- **Diff:** locate the existing `_get_model()` body. After the model is constructed (and before returning), add:
  ```python
  from apps.core.metrics import embedder_loaded
  embedder_loaded.set(1)
  ```
- **Note:** gauge is per-worker (each gunicorn process has its own REGISTRY). Sticky semantics — never set back to 0 (process exits before that matters). Documented in `apps/core/metrics.py` Phase 8a comment.
- **Verification:** `grep -n "embedder_loaded.set" apps/ingestion/embedder.py` returns one hit.
- **Effort:** 5 min.

### Step 3.6 — `apps/qdrant_core/search.py` search gauge + histogram

- **Goal:** observe `search_results_count` and set `search_threshold_used` on every successful search.
- **Diff:** at the end of `search()`, AFTER assembling the result dict:
  ```python
  from apps.core.metrics import search_results_count, search_threshold_used
  search_results_count.observe(float(result["total_candidates"]))
  search_threshold_used.set(float(result["threshold_used"]))
  ```
  (Or: import at module top; either is fine — recorder helpers in `metrics_recorders.py` cover http/grpc/pipeline; search reads metrics directly since it's only 2 lines.)
- **Verification:** `grep -n "search_results_count\|search_threshold_used" apps/qdrant_core/search.py` returns hits.
- **Effort:** 10 min.

### Step 3.7 — `tests/test_observability.py` update assertions

- **Goal:** assertions check the NEW positive shape (5 access-log keys post-ExtraAdder + counter behavior); existing assertions on absence either still pass or are tightened.
- **Diff (~5 minor edits):**
  - `test_request_completed_emitted_for_non_excluded_path` — already asserts `getattr(rec, "method", ...)` etc. With ExtraAdder applied, these kwargs survive into the LogRecord.
  - Add `test_request_id_on_healthz_present_post_polish` — `GET /healthz` now returns an `X-Request-ID` header (the narrowing). NOTE: `/healthz` is STILL excluded from access log; assertion is on the response header, not the log line.
  - Update `test_metrics_endpoint_excluded_from_request_id_header` — spec hard constraint #2 says `/metrics` ALSO keeps the header. Rename to `test_metrics_endpoint_includes_request_id_header_post_polish`; assert `X-Request-ID` IS present in the response. (Plan revision flagging this as A2.)
  - Optionally add `test_http_counter_increments_after_request` — pre-call counter sample, do request, post-call counter sample, assert delta ≥ 1. Use the global default REGISTRY; samples taken via `prometheus_client.generate_latest()` parsed for the line.
- **Verification:** `uv run pytest tests/test_observability.py -v` green.
- **Effort:** 30 min.

### Step 3.8 — Section 0 verification (host-equivalent)

- **Goal:** confirm Section 0 fixes work end-to-end before moving to ops work.
- **Commands:**
  ```
  uv run python manage.py check
  uv run pytest tests/test_observability.py -v
  uv run pytest -v   # full Phase 1-8a regression
  uv run ruff check . && uv run ruff format --check .
  ```
- **Live smoke (against running host stack on localhost:8080):**
  ```
  curl -i http://localhost:8080/healthz | grep -i x-request-id   # NOW present
  curl -sS http://localhost:8080/metrics | grep -E '_total\s+[1-9]' | head   # non-zero counters AFTER a few requests
  ```
- **Effort:** 15 min.

### Step 3.9 — `scripts/snapshot_qdrant.sh` (NEW)

- **Goal:** snapshot all collections (or one) with rotation.
- **Content (~80 lines, bash):**
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail

  : "${BACKUP_DIR_QDRANT:=/var/backups/qdrant}"
  : "${QDRANT_SNAPSHOT_KEEP:=7}"
  COLLECTION="${1:-}"   # optional CLI arg

  # Source .env to get QDRANT_API_KEY etc.
  if [ -f .env ]; then
      set -a; . ./.env; set +a
  fi
  : "${QDRANT_HTTP_PORT:=6333}"
  : "${QDRANT_API_KEY:?QDRANT_API_KEY must be set}"

  STAMP="$(date +%Y%m%dT%H%M%SZ)"
  OUT_DIR="${BACKUP_DIR_QDRANT}/${STAMP}"
  mkdir -p "${OUT_DIR}"

  cleanup_partial() { rm -rf "${OUT_DIR}"; }
  trap 'cleanup_partial' ERR

  base="http://localhost:${QDRANT_HTTP_PORT}"
  hdr=(-H "api-key: ${QDRANT_API_KEY}")

  if [ -z "${COLLECTION}" ]; then
      # all collections
      collections="$(curl -fsS "${hdr[@]}" "${base}/collections" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(c["name"] for c in d["result"]["collections"]))')"
  else
      collections="${COLLECTION}"
  fi

  for c in ${collections}; do
      echo "[snapshot] ${c}..."
      resp="$(curl -fsS -X POST "${hdr[@]}" "${base}/collections/${c}/snapshots")"
      name="$(echo "${resp}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')"
      curl -fsS "${hdr[@]}" -o "${OUT_DIR}/${c}__${name}" "${base}/collections/${c}/snapshots/${name}"
      echo "[snapshot] saved: ${OUT_DIR}/${c}__${name}"
  done

  trap - ERR

  # Rotation: keep last N
  cd "${BACKUP_DIR_QDRANT}"
  ls -1dt */ 2>/dev/null | tail -n +"$((QDRANT_SNAPSHOT_KEEP + 1))" | xargs -r rm -rf
  echo "[snapshot] kept last ${QDRANT_SNAPSHOT_KEEP} snapshot dirs"
  ```
- **CLI:** `bash scripts/snapshot_qdrant.sh [collection_name]` — no arg = all collections.
- **Verification:** dry-run via `bash -n scripts/snapshot_qdrant.sh` (syntax check); `chmod +x`; live invocation needs a running Qdrant.
- **Effort:** 30 min.

### Step 3.10 — `scripts/backup_postgres.sh` (NEW)

- **Goal:** `pg_dump -Fc` via `docker compose exec postgres` → host file with rotation.
- **Content (~50 lines):**
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail

  : "${BACKUP_DIR_POSTGRES:=/var/backups/postgres}"
  : "${POSTGRES_BACKUP_KEEP:=14}"

  if [ -f .env ]; then
      set -a; . ./.env; set +a
  fi
  : "${POSTGRES_DB:?POSTGRES_DB must be set}"
  : "${POSTGRES_USER:?POSTGRES_USER must be set}"

  STAMP="$(date +%Y%m%dT%H%M%SZ)"
  OUT="${BACKUP_DIR_POSTGRES}/qdrant_rag-${STAMP}.dump"
  mkdir -p "${BACKUP_DIR_POSTGRES}"

  echo "[backup] pg_dump -> ${OUT}"
  docker compose -f docker-compose.yml exec -T postgres \
      pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc \
      > "${OUT}"
  echo "[backup] saved: ${OUT}"

  # Rotation
  cd "${BACKUP_DIR_POSTGRES}"
  ls -1t qdrant_rag-*.dump 2>/dev/null | tail -n +"$((POSTGRES_BACKUP_KEEP + 1))" | xargs -r rm -f
  echo "[backup] kept last ${POSTGRES_BACKUP_KEEP} dumps"
  ```
- **Why `pg_dump -Fc`:** spec pitfall #8. Custom format is `pg_restore`-compatible.
- **Why `docker compose exec`:** host doesn't have `pg_dump` installed; the postgres container does. Uses `-T` to disable TTY (script-friendly).
- **Verification:** `bash -n scripts/backup_postgres.sh`; `chmod +x`.
- **Effort:** 25 min.

### Step 3.11 — `scripts/bootstrap.sh` (NEW)

- **Goal:** fresh-VM setup in one command. Idempotent. No relogin needed.
- **Content (~120 lines):**
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail

  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
      echo "[bootstrap] must run as root: sudo bash scripts/bootstrap.sh" >&2
      exit 2
  fi

  DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-}}"
  if [ -z "${DEPLOY_USER}" ]; then
      echo "[bootstrap] DEPLOY_USER not set and no SUDO_USER (don't run from root shell directly)" >&2
      exit 2
  fi

  echo "[bootstrap] deploy user: ${DEPLOY_USER}"

  # Preflight: deb-family
  if ! command -v apt-get >/dev/null 2>&1; then
      echo "[bootstrap] apt-get not found — bootstrap.sh supports Ubuntu/Debian only" >&2
      exit 2
  fi

  # Preflight: docker installed
  if ! command -v docker >/dev/null 2>&1; then
      echo "[bootstrap] installing docker.io + docker-compose-plugin..."
      apt-get update -y
      apt-get install -y --no-install-recommends docker.io docker-compose-plugin curl ca-certificates
  else
      echo "[bootstrap] docker already installed"
  fi

  # Preflight: docker daemon running
  if ! systemctl is-active --quiet docker; then
      echo "[bootstrap] starting docker daemon..."
      systemctl enable --now docker
  fi

  # Idempotent usermod
  if id -nG "${DEPLOY_USER}" | grep -qw docker; then
      echo "[bootstrap] ${DEPLOY_USER} already in docker group"
  else
      echo "[bootstrap] adding ${DEPLOY_USER} to docker group..."
      usermod -aG docker "${DEPLOY_USER}"
  fi

  # Idempotent .env copy
  if [ -f .env ]; then
      echo "[bootstrap] .env exists; skipping copy from .env.example"
  else
      cp .env.example .env
      chown "${DEPLOY_USER}:${DEPLOY_USER}" .env
      chmod 600 .env
      echo "[bootstrap] .env created from .env.example — EDIT secrets before make up!"
      echo "[bootstrap] aborting: set secrets in .env then re-run"
      exit 3
  fi

  # Idempotent BGE download
  if [ -d .bge_cache ] && [ "$(find .bge_cache -type f 2>/dev/null | wc -l)" -gt 100 ]; then
      echo "[bootstrap] .bge_cache populated; skipping bge-download"
  else
      echo "[bootstrap] running make bge-download as ${DEPLOY_USER} (5-15 min for ~4.5 GB)..."
      sg docker -c "su ${DEPLOY_USER} -c 'make bge-download'"
  fi

  # Idempotent stack up
  if sg docker -c "docker compose ps -q web" | grep -q .; then
      echo "[bootstrap] stack already up; skipping make up"
  else
      echo "[bootstrap] running make up as ${DEPLOY_USER}..."
      sg docker -c "su ${DEPLOY_USER} -c 'make up'"
  fi

  # Wait for healthz
  echo "[bootstrap] waiting for healthz (up to 180s)..."
  for i in $(seq 1 180); do
      if curl -fsS -m 2 http://localhost:"${HTTP_PORT:-8080}"/healthz >/dev/null 2>&1; then
          echo "[bootstrap] healthz green: $(curl -fsS http://localhost:"${HTTP_PORT:-8080}"/healthz)"
          echo "[bootstrap] DONE. Stack live on http://localhost:${HTTP_PORT:-8080}"
          exit 0
      fi
      sleep 1
  done
  echo "[bootstrap] healthz did not return green within 180s — see logs with 'make logs'" >&2
  exit 1
  ```
- **Idempotency rules:**
  - Already in docker group → skip usermod.
  - `.env` exists → DON'T overwrite. (First-run path: copy from `.env.example` then exit non-zero so operator edits secrets.)
  - `.bge_cache/` populated → skip download.
  - Stack already up → skip `make up`.
- **`sg docker -c`:** runs commands as if a re-login happened. Avoids "log out and back in" ritual. Inside `sg`, `su ${DEPLOY_USER}` runs as the deploy user.
- **Verification:** `bash -n scripts/bootstrap.sh`; `chmod +x`; idempotent re-run dry test (the first-run-no-.env path immediately exits 3 with the "edit secrets" message — clean).
- **Effort:** 60 min.

### Step 3.12 — `deploy/qdrant-rag.service` systemd unit (NEW)

- **Goal:** install-on-request systemd alternative to `make up`.
- **Content:**
  ```ini
  [Unit]
  Description=qdrant_rag (multi-tenant Qdrant vector storage)
  Requires=docker.service
  After=docker.service network-online.target

  [Service]
  Type=simple
  User=bol7
  Group=bol7
  WorkingDirectory=/home/bol7/Documents/BOL7/Qdrant
  EnvironmentFile=-/home/bol7/Documents/BOL7/Qdrant/.env
  ExecStartPre=/usr/bin/docker compose -f docker-compose.yml down --remove-orphans
  ExecStart=/usr/bin/docker compose -f docker-compose.yml up
  ExecStop=/usr/bin/docker compose -f docker-compose.yml down
  Restart=on-failure
  RestartSec=10
  TimeoutStartSec=300
  TimeoutStopSec=60

  [Install]
  WantedBy=multi-user.target
  ```
- **Top-of-file comment:** `# EDIT User=, Group=, WorkingDirectory= for your install`.
- **Why `User=bol7` not root:** spec lens-2 finding. Match the docker-group user (deploy user).
- **Why `Type=simple`:** `docker compose up` is a long-lived foreground command in non-detached mode (no `-d`). systemd treats the process as the unit's main process.
- **Verification:** `systemd-analyze verify deploy/qdrant-rag.service` reports clean (after path edits to match host).
- **Effort:** 20 min.

### Step 3.13 — `deploy/nginx/qdrant_rag.conf.example` (NEW)

- **Goal:** HTTP + gRPC reverse-proxy template.
- **Content (~80 lines):**
  ```nginx
  # qdrant_rag nginx template — copy + edit for your domain/certs

  upstream qdrant_rag_http {
      server 127.0.0.1:8080;
  }

  upstream qdrant_rag_grpc {
      server 127.0.0.1:50051;
  }

  server {
      listen 443 ssl http2;
      server_name qdrant.your-domain.com;

      ssl_certificate     /etc/letsencrypt/live/qdrant.your-domain.com/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/qdrant.your-domain.com/privkey.pem;

      client_max_body_size 50m;
      proxy_read_timeout 120s;
      proxy_connect_timeout 30s;
      proxy_send_timeout 120s;

      location /metrics {
          allow 10.0.0.0/8;
          allow 192.168.0.0/16;
          allow 172.16.0.0/12;
          deny all;
          proxy_pass http://qdrant_rag_http;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      }

      location /healthz {
          proxy_pass http://qdrant_rag_http;
          proxy_set_header Host $host;
      }

      location / {
          proxy_pass http://qdrant_rag_http;
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto https;
          proxy_set_header X-Request-ID $request_id;
      }
  }

  server {
      listen 50443 ssl http2;
      server_name grpc.your-domain.com;

      ssl_certificate     /etc/letsencrypt/live/grpc.your-domain.com/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/grpc.your-domain.com/privkey.pem;

      grpc_read_timeout 120s;
      grpc_send_timeout 120s;
      grpc_connect_timeout 30s;
      client_max_body_size 50m;

      location / {
          grpc_pass grpc://qdrant_rag_grpc;
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
- **Why `grpc_pass` (not `proxy_pass`):** spec pitfall #10. gRPC needs HTTP/2 termination + grpc-status/grpc-message headers on errors.
- **Why `.conf.example` extension:** spec lens-2 finding. nginx auto-loads `*.conf` from `conf.d/`; the `.example` suffix prevents accidental load-as-config. Operator copies and renames.
- **Verification:** `nginx -t -c <full_path>` (after copy + edits).
- **Effort:** 30 min.

### Step 3.14 — `Makefile` add 3 targets

- **Goal:** `snapshot`, `backup`, `load-test` Make targets + help block.
- **Diff:**
  - Add to `.PHONY` line.
  - Add help printf lines after the existing "Backup / restore (manual; documented in RUNBOOK)" header (or create the section).
  - Add target rules:
    ```makefile
    snapshot:
    	bash scripts/snapshot_qdrant.sh

    backup:
    	bash scripts/backup_postgres.sh

    load-test:
    	@echo "[load-test] requires stack up; see RUNBOOK §6"
    	python scripts/load_test.py
    ```
- **Why `load-test` not `make run`:** spec hard constraint — `scripts/load_test.py` is HOST-side (uses host httpx + targets `localhost:8080`). Don't shell into the container.
- **Verification:** `make help | grep -E "snapshot|backup|load-test"` shows the lines.
- **Effort:** 15 min.

### Step 3.15 — `docker-compose.yml` `stop_grace_period: 30s` on grpc

- **Goal:** Compose's stop wait window > app's `GRPC_SHUTDOWN_GRACE_SECONDS=10`. Buffer for log flush + container teardown.
- **Diff:** in the `grpc:` service block, add `stop_grace_period: 30s` (any field-order; near `restart: unless-stopped`).
- **Verification:** `docker compose -f docker-compose.yml config | grep -A1 'grpc:' | grep stop_grace_period`.
- **Effort:** 5 min.

### Step 3.16 — `.env.example` document new env vars

- **Goal:** make new ops env vars discoverable.
- **Diff:** append:
  ```
  # ── Backups (Phase 8b) ──────────────────────────────────────────────
  BACKUP_DIR_QDRANT=/var/backups/qdrant
  BACKUP_DIR_POSTGRES=/var/backups/postgres
  QDRANT_SNAPSHOT_KEEP=7
  POSTGRES_BACKUP_KEEP=14

  # ── Bootstrap (Phase 8b) ────────────────────────────────────────────
  # DEPLOY_USER overrides the user that bootstrap.sh adds to the docker group.
  # Defaults to $SUDO_USER (the operator who ran sudo).
  # DEPLOY_USER=
  ```
- **Effort:** 5 min.

### Step 3.17 — `RUNBOOK.md` (NEW, 10 sections)

- **Goal:** operational truth, not architectural prose.
- **Sections (in this order):**
  1. **Deploy a fresh host** — `bash scripts/bootstrap.sh`; first-run vs subsequent-run; expected timing (15-25 min).
  2. **Upgrade an existing deploy** — `git pull && make rebuild && make ps && make health`.
  3. **Rollback** — v1 has no image tags; rollback = `git checkout <prev_sha>` + `make rebuild`. Phase 9+ adds tagged images.
  4. **Restart the stack** — `make restart` (down + up); does NOT use Compose `restart` (preserves volumes).
  5. **Logs** — `make logs` (web), `make logs grpc`, `make logs postgres`, `docker compose logs --tail 100 -f` for combined.
  6. **Metrics** — `curl http://localhost:8080/metrics` (or via nginx); list of 8 metrics + meanings; recommended Prometheus alerts.
  7. **Restore Postgres** — `pg_restore -d qdrant_rag /var/backups/postgres/<file>.dump`; mention stack-down requirement; verify with `make run python manage.py migrate --plan`.
  8. **Restore Qdrant** — `curl -X PUT api/snapshots/upload`; restore one collection at a time; verify via `make run python scripts/verify_setup.py --full`.
  9. **Rotate secrets** — edit `.env`, then `make rebuild` (env is read at container start). Rotate `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `QDRANT_API_KEY` separately. Each ends with `make health` verification.
  10. **Failure modes** — top 5 (BGE-M3 cache permission, port conflict on 5432/6379, Qdrant gRPC handshake error on cold start, healthz=503 in initial 30s window, `make rebuild` invalidating `bge_cache` volume); for each: cause + fix.
- **Each section pattern:** "Do these commands in this order"; expected output snippet; "verify success by:" tail.
- **Length:** 300-500 lines (spec hard constraint #22).
- **Effort:** 90 min (longest single artifact).

### Step 3.18 — `.github/workflows/ci.yml` (NEW)

- **Goal:** PR + push-to-main gating.
- **Content (~70 lines, single Python 3.13 matrix):**
  ```yaml
  name: CI
  on:
    pull_request:
    push:
      branches: [main]
  jobs:
    lint-and-test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: astral-sh/setup-uv@v3
          with:
            version: latest
        - name: Cache uv
          uses: actions/cache@v4
          with:
            path: |
              ~/.cache/uv
              .venv
            key: uv-${{ runner.os }}-${{ hashFiles('uv.lock', 'pyproject.toml') }}
        - run: uv sync --frozen
        - run: uv run ruff check .
        - run: uv run ruff format --check .
        - name: migrations-check
          run: DJANGO_SETTINGS_MODULE=tests.test_settings uv run python manage.py makemigrations --check --dry-run
        - run: uv run pytest -v -m 'not embedder' --maxfail=10
  ```
- **Why no Postgres/Qdrant service containers:** spec lens-2 finding. Tests use `tests/test_settings.py` SQLite overlay; integration tests against Qdrant skip-graceful when unreachable. Single-environment CI matches `make test` locally. Phase 9+ can add service containers if needed.
- **Why `cache@v4` keyed on uv.lock + pyproject.toml:** spec pitfall #12.
- **Why `not embedder`:** the 9-21 BGE-M3 tests skip-graceful on hosts without the cache, but `pytest -m 'not embedder'` skips them deterministically by marker.
- **Verification:** push a branch + open PR; or run `act` locally for a dry-run.
- **Effort:** 30 min.

### Step 3.19 — `README.md` mark Phase 8b complete + Deployment section

- **Goal:** README's status table marks 8a + 8b complete; Deployment section points to RUNBOOK.
- **Diff:** find the Phase 8 row in the status table; mark complete with date. Add a new top-level section after "Architecture":
  ```
  ## Deployment

  Fresh host setup (Ubuntu 24.04 / Debian 12):

      sudo bash scripts/bootstrap.sh

  Day-to-day operations: see [RUNBOOK.md](./RUNBOOK.md).

  Backups:

      make snapshot   # Qdrant
      make backup     # Postgres

  Optional systemd unit at `deploy/qdrant-rag.service`. Optional nginx template at `deploy/nginx/qdrant_rag.conf.example`.
  ```
- **Effort:** 15 min.

### Step 3.20 — Final regression

- **Commands:** `uv run pytest -v`; `uv run ruff check .`; `uv run ruff format --check .`; `make help`; `bash -n scripts/*.sh`.
- **Effort:** 15 min.

---

## 4. Risk register

### R1 [critical] — Counter wiring causes test fixtures to fail
Existing `test_observability.py` uses the global default REGISTRY. Counter increments from `record_http_request` will accumulate across tests in the same session. Acceptable IF tests assert "delta ≥ 1" rather than absolute values.

**Mitigation:** Step 3.7 commits to delta-based assertions; no `CollectorRegistry` swap needed. Spec pitfall #1.

### R2 [critical] — `record_http_request` called on excluded paths
If the recorder call is OUTSIDE the path-exclusion check (e.g., in a separate middleware), `/metrics` scrapes increment `qdrant_rag_http_requests_total{endpoint="metrics"}` on every Prometheus pull → self-feedback loop.

**Mitigation:** Step 3.3 commits the recorder call to `AccessLogMiddleware.__call__` finally block, AFTER the path-exclusion check (which early-returns). Same exclusion as access log. Spec pitfall #2.

### R3 [critical] — `ExtraAdder` ordering
Must come AFTER `add_log_level` (which wants a clean event_dict) and BEFORE `_request_context_processor` (which calls `setdefault`). Wrong order = either KeyError or dropped fields.

**Mitigation:** Step 3.1 commits the position; verification asserts via name list.

### R4 [critical] — Phase timer ms vs Prometheus seconds
Phase 8a's `timer(...)` records milliseconds. Prometheus histograms expect seconds. Recorder dispatch MUST convert.

**Mitigation:** Step 3.3 commits `duration_seconds = duration_ms / 1000.0` and `phase_ms / 1000.0` in the for-loop.

### R5 [major] — `bootstrap.sh` runs `make up` without secrets edited
First-run path copies `.env.example` → `.env`. If the script proceeds to `make up`, postgres starts with the placeholder password, BGE downloads against a placeholder QDRANT_API_KEY, etc.

**Mitigation:** Step 3.11 commits the first-run path to `exit 3` with a "edit secrets in .env then re-run" message. Idempotent re-run sees `.env` exists and proceeds normally.

### R6 [major] — `bootstrap.sh` BGE download time
~5-15 min for ~4.5 GB. CI runners or impatient operators may kill it. Spec pitfall #5.

**Mitigation:** Step 3.11 commits a progress banner (the make target prints download progress); script doesn't impose its own timeout; documented in RUNBOOK §1 as expected duration.

### R7 [major] — Snapshot script doesn't clean up partial snapshots
If `curl` fails mid-snapshot, the dir contains partial data. Subsequent rotation logic counts it as a successful snapshot, displacing a real one.

**Mitigation:** Step 3.9 commits `set -e` + `trap 'cleanup_partial' ERR` to remove the partial dir before exiting non-zero. Spec pitfall #7.

### R8 [major] — Postgres backup uses plain SQL dump
`pg_dump` defaults to plain SQL, which restores via `psql`, NOT `pg_restore`. RUNBOOK §7 documents `pg_restore`; without `-Fc`, the file is the wrong format.

**Mitigation:** Step 3.10 commits `pg_dump ... -Fc`. Spec pitfall #8.

### R9 [major] — systemd unit `WorkingDirectory` hardcoded
Operators on different hosts have different paths. Hardcoding `/home/bol7/...` works for the spec author's box but breaks elsewhere.

**Mitigation:** Step 3.12 commits a top-of-file comment instructing operators to edit `User=`, `Group=`, `WorkingDirectory=`. RUNBOOK §1 mentions. Spec pitfall #9.

### R10 [major] — nginx config uses `proxy_pass` for gRPC
`proxy_pass` doesn't terminate HTTP/2 properly for gRPC; clients see TCP-level errors instead of gRPC status codes.

**Mitigation:** Step 3.13 commits `grpc_pass grpc://qdrant_rag_grpc;`. Spec pitfall #10.

### R11 [major] — CI cache key forgets `pyproject.toml`
Cache invalidates only on `uv.lock` SHA. If `pyproject.toml` changes without `uv lock` (unusual but possible), cache hits stale artifacts.

**Mitigation:** Step 3.18 commits `key: uv-${{ runner.os }}-${{ hashFiles('uv.lock', 'pyproject.toml') }}`. Spec pitfall #12.

### R12 [major] — `_record_metrics` decorator status code mishandling
gRPC handlers use `context.abort(code, msg)` which raises `RpcError`. Decorator must capture the code before re-raising; default to OK on normal return, INTERNAL on uncaught exception.

**Mitigation:** Step 3.4 commits the try/except branches. Verification: existing `test_search_grpc.py` validation tests still green (they assert specific error codes on `context.abort` paths).

### R13 [major] — Phase 8a tests assert on `/metrics` X-Request-ID absence
Spec hard constraint #2 says `/metrics` keeps the header (only `/static/` is excluded from RequestID). The Phase 8a test `test_metrics_endpoint_excluded_from_request_id_header` asserted `header is None` — now WRONG.

**Mitigation:** Step 3.7 renames + flips the assertion to `header is not None`. Spec pitfall #16.

### R14 [minor] — Counter reset on rebuild
Each container restart resets all counters to 0 (per-process REGISTRY). Operators looking at "all-time" counts via `/metrics` see only "since last rebuild" data.

**Mitigation:** RUNBOOK §6 documents this. Long-term aggregation lives in Prometheus storage.

### R15 [minor] — `embedder_loaded` per-worker visibility
gunicorn runs N workers; each has its own gauge. `/metrics` from a single worker shows only that worker's gauge → load balancer round-robin → metric jitters.

**Mitigation:** Phase 8a's `apps/core/metrics.py` already documents. RUNBOOK §6 mentions.

### R16 [minor] — `search_threshold_used` overwrite per call
The gauge is set on every search; multi-tenant traffic with different thresholds (none in v1) would cause thrashing. v1 has a single fixed threshold per search invocation, so this is benign.

### R17 [minor] — Nginx config `.conf.example` not auto-loaded
Operators copying to `/etc/nginx/conf.d/` MUST rename to drop `.example`. `nginx -t` would otherwise silently skip the file.

**Mitigation:** RUNBOOK §1 (Deploy section) and the file's top-of-file comment instruct.

### R18 [minor] — CI doesn't run security scan
`pip-audit` / `safety` / dependency-review-action are out of scope. Phase 9+ may add.

**Mitigation:** documented as out-of-scope.

### R19 [minor] — Backup files unencrypted
`pg_dump -Fc` produces unencrypted output. Operators wanting offsite encrypted backups should pipe to `gpg --symmetric` or use AWS S3 SSE.

**Mitigation:** RUNBOOK §7 mentions in passing; v1 acceptable.

---

## 5. Verification checkpoints

| # | Checkpoint | Command | Expected |
|---|---|---|---|
| 5.1 | logging.py ExtraAdder present | `uv run python -c "from apps.core.logging import _SHARED_PROCESSORS; print('ExtraAdder' in [type(p).__name__ for p in _SHARED_PROCESSORS])"` | True |
| 5.2 | metrics_recorders importable | `uv run python -c "from apps.core.metrics_recorders import record_http_request, record_grpc_request, record_pipeline_phase; print('ok')"` | "ok" |
| 5.3 | Middleware exclusion lists narrowed | `uv run python -c "from apps.core.middleware import _REQUESTID_EXCLUDED_PATHS, _ACCESSLOG_EXCLUDED_PATHS; print(_REQUESTID_EXCLUDED_PATHS, _ACCESSLOG_EXCLUDED_PATHS)"` | `('/static/',)` and 4-tuple |
| 5.4 | gRPC handler decorator | `grep -n "_record_metrics" apps/grpc_service/handler.py` | 3+ hits (decorator def + 2 usages) |
| 5.5 | embedder_loaded set | `grep -n "embedder_loaded.set" apps/ingestion/embedder.py` | 1 hit |
| 5.6 | search gauges | `grep -nE "search_results_count\|search_threshold_used" apps/qdrant_core/search.py` | 2+ hits |
| 5.7 | manage.py check after Section 0 | `uv run python manage.py check` | exit 0 |
| 5.8 | test_observability.py green | `uv run pytest tests/test_observability.py -v` | green |
| 5.9 | Full host regression after Section 0 | `uv run pytest -v` | 164+ passed (Phase 8a baseline; +0 net for Section 0) |
| 5.10 | scripts syntax-check | `bash -n scripts/{snapshot_qdrant,backup_postgres,bootstrap}.sh` | exit 0 each |
| 5.11 | systemd unit syntax | `systemd-analyze verify deploy/qdrant-rag.service` (or skip if unable) | clean |
| 5.12 | nginx config syntax | `nginx -t -c <full_path>` (or PASS-via-equivalent) | clean |
| 5.13 | Makefile new targets | `make help` | shows snapshot/backup/load-test |
| 5.14 | Compose stop_grace_period | `docker compose -f docker-compose.yml config \| grep stop_grace_period` | "30s" on grpc service |
| 5.15 | .env.example has new keys | `grep -E "BACKUP_DIR\|SNAPSHOT_KEEP\|BACKUP_KEEP\|DEPLOY_USER" .env.example` | 5 hits |
| 5.16 | RUNBOOK exists, all 10 sections | `grep -c "^## " RUNBOOK.md` | ≥10 |
| 5.17 | CI workflow YAML valid | `yamllint .github/workflows/ci.yml` (optional) or push-trigger via PR | clean |
| 5.18 | README mentions 8b done + Deployment | `grep -E "Phase 8b|Deployment" README.md` | hits |
| 5.19 | Final regression | `uv run pytest -v` + `uv run ruff check .` + `uv run ruff format --check .` | all green |
| 5.20 | Live /metrics non-zero counters (host stack) | `curl -sS localhost:8080/metrics \| grep -E '_total\s+[1-9]'` after a few requests | matches |
| 5.21 | Live /healthz X-Request-ID echo | `curl -i localhost:8080/healthz \| grep -i x-request-id` | header present |

---

## 6. Spec ambiguities & open questions

### A1 — `tests/test_observability.py` update scope
Step 3.7 commits to: replace absence-asserts with positives; add a new test for `/healthz` X-Request-ID; add a new test for counter delta. Existing absence-asserts on access log shape REMAIN PASSING (they were checking absence of fields like `tenant_id` on /metrics, which still don't get there because /metrics is excluded). The polish unblocks NEW positive assertions.

### A2 — `/metrics` X-Request-ID spec re-read
Spec hard constraint #2 says `/metrics` "keeps the header" with rationale "Prometheus scrapers ignore it but it's harmless." Phase 8a's test asserted absence (matching the old over-exclusion). Step 3.7 flips the assertion. This is a contract change observable to scrapers; documented in implementation report.

### A3 — `bootstrap.sh` package manager assumption
Spec pitfall #4: deb-family only. Step 3.11 commits an early `apt-get` check; non-deb hosts get a stub error pointing to manual install. Acceptable per spec.

### A4 — Snapshot rotation when zero existing
`ls -1dt */ | tail -n +"$((QDRANT_SNAPSHOT_KEEP + 1))"` returns empty when fewer than KEEP+1 dirs exist; `xargs -r rm -rf` is a no-op. Safe; spec defaults to 7 keeps.

### A5 — Counter wiring placement (middleware finally vs view)
Step 3.3 commits to `AccessLogMiddleware.__call__` finally block. Why not in views: views are 4 distinct files (upload/delete/search HTTP + healthz); centralizing in middleware avoids 4-way duplication and ensures non-routed paths also get counted (as `endpoint="unknown"`).

### A6 — CI matrix scope
Step 3.18 commits to single Python 3.13. No matrix expansion. Adding 3.12/3.11 requires verifying Phase 1's Django version supports them; out of scope.

### A7 — `embedder_loaded` gauge in test fixtures
Pipeline tests use `mock_embedder` fixture that mocks `embed_passages` directly; `_get_model()` is never called in those tests. So `embedder_loaded` stays at its module-load default (0) during pipeline tests. Acceptable — `embedder_loaded` is observed in production traffic + verify_setup.py --full path.

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope (post-v1, never)

- Container registry + tagged images
- Auth, TLS termination inside service, Celery activation, Redis cache, audit log
- Quantization, per-tenant config, multi-host orchestration
- Auto-scaling, blue/green deploys
- Grafana dashboard JSON, Alertmanager rules, security scans

### Phase 8b explicit modifies (5) + new (8) + tests (1)

- **Modified:** `apps/core/middleware.py`, `apps/core/logging.py`, `apps/grpc_service/handler.py`, `apps/ingestion/embedder.py`, `apps/qdrant_core/search.py`, `Makefile`, `docker-compose.yml`, `.env.example`, `README.md`, `tests/test_observability.py`.
- **New:** `apps/core/metrics_recorders.py`, `scripts/{snapshot_qdrant,backup_postgres,bootstrap}.sh`, `deploy/qdrant-rag.service`, `deploy/nginx/qdrant_rag.conf.example`, `RUNBOOK.md`, `.github/workflows/ci.yml`, `build_prompts/phase_8b_ops_deployment/{plan,plan_review,implementation_report}.md`.

(That's 10 modified + 8 new + 3 reports. Spec lists "8 new + 3 modified" for Section 1 + "4 modified" for Section 0 = 8 new + 7 modified + 1 test. Plan adds `apps/qdrant_core/search.py` from spec's Section 0 file list = 8 new + 8 modified + 1 test. README is one of those 8 modified.)

### Don't touch

- `apps/core/{apps,__init__,urls,views,timing}.py`
- `apps/qdrant_core/{client,collection,exceptions,naming}.py`
- `apps/grpc_service/{__init__,apps,server,generated}.py`
- `apps/tenants/`, `apps/documents/{models,admin,serializers,urls,exceptions,views,migrations}.py`
- `apps/ingestion/{chunker,payload,locks,pipeline}.py` (pipeline.py timer wrap from 8a stays)
- `proto/`, `scripts/{compile_proto,verify_setup,load_test}.py`
- `Dockerfile`
- `tests/test_*.py` other than `test_observability.py`

---

## 8. Acceptance-criteria mapping

| # | Criterion | Step | Verify | Expected |
|---|---|---|---|---|
| 1 | `/healthz` includes X-Request-ID | 3.3 | step 5.21 | header present in response |
| 2 | Access log line has 5 keys | 3.1, 3.3 | step 5.8 + manual `make logs` | method/path/status_code/duration_ms/phases all present |
| 3 | `/metrics` shows non-zero counters | 3.2-3.6 | step 5.20 | `_total\s+[1-9]` matches |
| 4 | `embedder_loaded` flips to 1 | 3.5 | manual `/metrics` after embed | gauge value 1 |
| 5 | gRPC counters increment | 3.4 | manual after grpcurl Search | gauge value ≥1 |
| 6 | `bash scripts/bootstrap.sh` provisions fresh VM | 3.11 | manual on VM | DONE message + healthz green |
| 7 | bootstrap idempotent | 3.11 | re-run on bootstrapped host | "already up; skipping" lines |
| 8 | snapshot script + rotation | 3.9 | run script + `ls $BACKUP_DIR_QDRANT` | last 7 dirs |
| 9 | backup script + rotation | 3.10 | run script + `ls $BACKUP_DIR_POSTGRES` | last 14 dumps |
| 10 | `make snapshot/backup` wired | 3.14 | step 5.13 | targets shell out |
| 11 | systemd unit installs cleanly | 3.12 | `systemctl daemon-reload` (manual) + step 5.11 | clean |
| 12 | nginx config parses | 3.13 | step 5.12 | clean |
| 13 | CI runs ruff + tests, passes | 3.18 | step 5.17 + push to branch | green |
| 14 | Compose has stop_grace_period: 30s on grpc | 3.15 | step 5.14 | "30s" on grpc |
| 15 | RUNBOOK has 10 sections | 3.17 | step 5.16 | ≥10 H2 headings |
| 16 | README marks 8b complete | 3.19 | step 5.18 | hits on "Phase 8b" + "Deployment" |
| 17 | Phase 1-8a regression | 3.20 | step 5.19 | all prior green |
| 18 | `make rebuild && make ps && make health` post-Phase-8b | 3.20 | manual | 6 healthy |

---

## 9. Tooling commands cheat-sheet

```bash
# Section 0 verify
uv run python manage.py check
uv run pytest tests/test_observability.py -v
uv run pytest -v   # full Phase 1-8a regression

# Section 1 syntax checks
bash -n scripts/snapshot_qdrant.sh scripts/backup_postgres.sh scripts/bootstrap.sh
systemd-analyze verify deploy/qdrant-rag.service   # may require sudo
nginx -t -c $(pwd)/deploy/nginx/qdrant_rag.conf.example   # PASS-via-equivalent if nginx not installed
yamllint .github/workflows/ci.yml   # optional

# Live smoke (host stack)
curl -i http://localhost:8080/healthz | grep -i x-request-id
curl -sS http://localhost:8080/metrics | grep -E '_total\s+[1-9]' | head
make logs web --tail 100 | grep request_completed | head
make help | grep -E "snapshot|backup|load-test"

# Final regression
uv run ruff check . && uv run ruff format --check .
uv run pytest -v

# Out-of-scope mtime audit (no git in repo)
find apps/core/{apps,__init__,urls,views,timing}.py \
     apps/qdrant_core/{client,collection,exceptions,naming}.py \
     apps/grpc_service/{__init__,apps,server}.py \
     apps/grpc_service/generated apps/tenants apps/documents \
     apps/ingestion/{chunker,payload,locks,pipeline}.py \
     proto Dockerfile pyproject.toml uv.lock \
     scripts/{compile_proto,verify_setup,load_test}.py \
     tests \
     -newer build_prompts/phase_8a_code_hardening/implementation_report.md \
     2>/dev/null
# expect empty (or only test_observability.py if updated in step 3.7)
```

---

## 10. Estimated effort

| Step | Estimate |
|---|---|
| 3.1 logging.py ExtraAdder | 10 min |
| 3.2 metrics_recorders.py | 10 min |
| 3.3 middleware.py narrow + counter wiring | 25 min |
| 3.4 handler.py decorator | 25 min |
| 3.5 embedder.py gauge | 5 min |
| 3.6 search.py gauge + histogram | 10 min |
| 3.7 test_observability.py update | 30 min |
| 3.8 Section 0 verification | 15 min |
| 3.9 snapshot_qdrant.sh | 30 min |
| 3.10 backup_postgres.sh | 25 min |
| 3.11 bootstrap.sh | 60 min |
| 3.12 systemd unit | 20 min |
| 3.13 nginx template | 30 min |
| 3.14 Makefile targets | 15 min |
| 3.15 docker-compose.yml stop_grace_period | 5 min |
| 3.16 .env.example new keys | 5 min |
| 3.17 RUNBOOK.md (10 sections) | 90 min |
| 3.18 CI workflow | 30 min |
| 3.19 README.md update | 15 min |
| 3.20 final regression | 15 min |
| 3.21 implementation_report.md (Prompt 3) | 30 min |
| **Total** | **~7 hours** |

Section 0 alone: ~2 hours. Section 1: ~5 hours (RUNBOOK is the longest individual artifact at 90 min).

---

## End of plan
