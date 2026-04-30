# Phase 8b — Operational Artifacts + Phase 8a Polish

> **Audience:** A coding agent building on top of verified-green Phases 1–8a at `/home/bol7/Documents/BOL7/Qdrant`. Phase 8b is the **final phase** of the v1 build. After 8b ships green, qdrant_rag is operable on a real production host.

---

## Mission

Make the running app **deployable, recoverable, observable from the outside, and gated on green CI**. Two scope sections in one phase:

**Section 0 — Phase 8a polish.** Three small bugs found during 8a verification: `/healthz` was over-excluded from `RequestIDMiddleware`; the structlog config is missing `ExtraAdder` so stdlib `logger.info(..., extra={...})` kwargs don't render; Prometheus counter increments aren't wired into recording sites (the metric families exist but stay at zero).

**Section 1 — Operational artifacts.** Backup + restore scripts; fresh-VM bootstrap that avoids the manual `usermod + relogin` ritual; nginx reverse-proxy templates for HTTP + gRPC; systemd unit as alternative deployment path; RUNBOOK covering deploy/upgrade/restore/secret-rotation; GitHub Actions CI workflow; Makefile shortcuts; one-line Compose tweak for graceful-shutdown timing.

After Phase 8b: a fresh Ubuntu/Debian VM bootstrap is one command (`bash scripts/bootstrap.sh`); operators manage the stack via either `make` or `systemctl`; PRs are gated on green CI; every Prometheus metric increments on real traffic; the access log carries timing+status data; nginx fronts both HTTP and gRPC; backups run on a documented cadence.

---

## Why now

- Phase 8a delivered the in-process observability infrastructure (middleware, metrics, ContextVars, signal handler). It works but has three small wiring gaps surfaced during live verification.
- Phase 8b delivers the operational shell: deployment, backup, RUNBOOK, CI. After this phase, v1 is shippable.
- Splitting 8a/8b kept each phase under ~12 files. Combining them would have made Phase 8 unreviewable.

---

## Read first

1. `README.md` — current API surface and architecture.
2. `build_prompts/phase_8a_code_hardening/spec.md` — full 8a spec (the polish in Section 0 is gap-fixing against this).
3. `build_prompts/phase_8a_code_hardening/implementation_report.md` — 8a outcomes; lists the three deviations Phase 8b must close.
4. `build_prompts/phase_7_search_grpc/spec.md` — gRPC server contract; gRPC counter wiring respects it.
5. `build_prompts/phase_5b_upload_idempotency/spec.md` — pipeline phases; the `pipeline_phase_duration_seconds` histogram observes them.
6. `apps/core/middleware.py` — current middleware (over-excludes `/healthz` from RequestID; needs narrowing).
7. `apps/core/logging.py` — current structlog config (missing `ExtraAdder`).
8. `apps/core/metrics.py` — current metric registry (recorder helpers don't exist yet).
9. `apps/grpc_service/handler.py` — current gRPC handler (no metric wiring).
10. `apps/ingestion/{pipeline,embedder}.py` — recording sites for pipeline phase histograms and the `embedder_loaded` gauge.
11. `Makefile` — current target list.
12. `docker-compose.yml` — current `grpc` service definition (no `stop_grace_period`).
13. `.env`, `.env.example` — current env var inventory.

---

## Hard constraints

1. **8a polish before ops work.** Section 0 fixes ship in earlier steps so verification of new ops scripts (which exercise metrics + access log) sees the corrected behavior.

2. **`RequestIDMiddleware` exclusion list narrows to `/static/` only.** `/healthz` and `/admin/` keep the request_id (correlation IDs are useful when probes fail or admin pages misbehave). `/metrics` ALSO keeps the header — Prometheus scrapers ignore it but it's harmless.

3. **`AccessLogMiddleware` exclusion list stays as before:** `/metrics`, `/healthz`, `/static/`, `/admin/` (high-volume; logging each entry would dominate the log stream).

4. **`structlog.stdlib.ExtraAdder()` added to BOTH processor chains in `apps/core/logging.py`** — the foreign_pre_chain (used by stdlib loggers via `ProcessorFormatter`) AND the `structlog.configure(processors=[...])` chain (used by structlog-native loggers). Place AFTER `add_log_level` and BEFORE the `_request_context_processor`.

5. **Counter recorder helpers live in `apps/core/metrics_recorders.py`** (new file). Three thin functions:
   - `record_http_request(method: str, endpoint: str, status_code: int, duration_seconds: float)` — increments `http_requests_total`, observes `http_request_duration_seconds`. Called from `AccessLogMiddleware` AFTER the access log emit (same path-exclusion rules apply — recorder is only called when the access log line is emitted).
   - `record_grpc_request(rpc: str, status_code: str, duration_seconds: float)` — increments `grpc_requests_total`, observes `grpc_request_duration_seconds`.
   - `record_pipeline_phase(phase: str, duration_seconds: float)` — observes `pipeline_phase_duration_seconds`. Called from the access log middleware iterating the timer dict (so all phases recorded in one place, not at each `with timer(...)` site).

6. **`embedder_loaded` gauge set in `apps/ingestion/embedder._get_model()`** — `embedder_loaded.set(1)` immediately after the model load completes. The gauge stays sticky (never set back to 0).

7. **`search_threshold_used` gauge + `search_results_count` histogram set in `apps.qdrant_core.search.search()`** — on every successful search response, set/observe the values from the response dict.

8. **gRPC counter wiring via decorator.** A small `_record_metrics` decorator wraps each RPC method (`Search`, `HealthCheck`). Decorator captures `grpc.StatusCode` from `context.set_code(...)` calls or exceptions; default `OK`. Records to `grpc_requests_total.labels(rpc=, status_code=).inc()`.

9. **bootstrap.sh runs as root.** Documented requirement: `sudo bash scripts/bootstrap.sh`. Uses `usermod -aG docker <user>` for the `DEPLOY_USER` (env var, default current `$SUDO_USER`), then `sg docker -c "<commands>"` for first compose-up so no relogin needed mid-script.

10. **bootstrap.sh is idempotent.** Re-running on an already-bootstrapped host: skips usermod if user already in docker group; skips `.env` copy if `.env` exists; skips bge-download if `.bge_cache/` looks populated; just runs `make up && make health`.

11. **bootstrap.sh verifies success.** Final step: `make health` must return `{"status": "ok", ...}` within 180 seconds. If it doesn't, exit non-zero with a "see logs with `make logs`" message.

12. **Snapshot/backup scripts use env-configurable destinations.**
    - `BACKUP_DIR_QDRANT` (default `/var/backups/qdrant`)
    - `BACKUP_DIR_POSTGRES` (default `/var/backups/postgres`)
    - Scripts create directories if missing, owned by current user.

13. **Snapshot/backup rotation is built-in.**
    - Qdrant snapshots: keep last 7 (env var `QDRANT_SNAPSHOT_KEEP=7`)
    - Postgres backups: keep last 14 (env var `POSTGRES_BACKUP_KEEP=14`)
    - Older files deleted by the script after a successful new write.

14. **Snapshot script targets one collection or all.** CLI: `bash scripts/snapshot_qdrant.sh [collection_name]`. With no arg, snapshots ALL collections via `GET /collections` enumeration.

15. **systemd unit is OPTIONAL alternative.** RUNBOOK documents both paths. Default operator workflow: `make`. systemd unit (`deploy/qdrant-rag.service`) is install-on-request:
    ```
    sudo cp deploy/qdrant-rag.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now qdrant-rag
    ```
    Operator only needs NOPASSWD `systemctl restart qdrant-rag` if they go this route.

16. **nginx config is a TEMPLATE.** Operators copy and edit. Placeholders clearly marked: `qdrant.your-domain.com`, `grpc.your-domain.com`, cert paths. `client_max_body_size 50m`, `proxy_read_timeout 120s`, `grpc_read_timeout 120s`. `/metrics` location uses `allow 10.0.0.0/8; allow 192.168.0.0/16; deny all;` for internal-only scraping.

17. **nginx config does NOT replace the docker-compose stack's port bindings.** It sits in front of `localhost:8080` (HTTP) and `localhost:50051` (gRPC).

18. **Compose `grpc` service gets `stop_grace_period: 30s`.** App's `GRPC_SHUTDOWN_GRACE_SECONDS` defaults to 10s; Compose's wait window must exceed that with buffer. 30s = 10s app drain + 20s buffer for log flush + container teardown.

19. **CI workflow runs ruff + tests against Postgres + Qdrant service containers.** BGE-M3 mocked. The 21 host-blocked tests skip in CI; the 1 `test_500_envelope` known failure stays a known failure (not gating). `migrations-check` is a separate step.

20. **CI cache for `uv.lock`** via `actions/cache@v4` keyed on `uv.lock` SHA. Speeds re-runs.

21. **CI does NOT publish images.** No registry push. (Phase 9+ adds tagged images + registry; out of scope for v1.)

22. **RUNBOOK is operationally useful, not aspirational.** Each section: "do these commands in this order," with expected output snippets, with "verify success by:" tail. NO architectural prose (that's what the README is for). Length: 300-500 lines.

23. **Makefile new targets:**
    - `make snapshot` → `bash scripts/snapshot_qdrant.sh`
    - `make backup` → `bash scripts/backup_postgres.sh`
    - `make load-test` → `python scripts/load_test.py` (assumes stack up; documented in target's help string)

24. **No new Python dependencies.** Counter wiring uses already-installed `prometheus-client`. Recorder helpers are pure stdlib + `prometheus-client`.

25. **No schema change, no migration.** Section 0 fixes are code-only.

26. **`make rebuild` (not `make wipe`)** preserves volumes during the test cycle.

27. **Phase 1-8a regression: full suite stays green.** Section 0 fixes may need test updates (the access log line now contains more fields — existing tests that asserted `assert "method" not in log` would break, but nothing should be asserting on absence). Verify carefully.

---

## Files modified / created

### Section 0 — 8a polish (4 files modified)

1. `apps/core/middleware.py` — narrow `_REQUESTID_EXCLUDED_PATHS` to `("/static/",)` only; keep `_ACCESSLOG_EXCLUDED_PATHS` as is; integrate counter recorder calls
2. `apps/core/logging.py` — add `structlog.stdlib.ExtraAdder()` to both processor chains
3. `apps/core/metrics_recorders.py` — NEW: three thin recorder helpers
4. `apps/grpc_service/handler.py` — add `_record_metrics` decorator; wrap `Search` and `HealthCheck`
5. `apps/ingestion/embedder.py` — set `embedder_loaded.set(1)` post-load
6. `apps/qdrant_core/search.py` — set `search_threshold_used` gauge + observe `search_results_count` histogram on success

### Section 1 — Operational artifacts (8 new + 3 modified)

7. `scripts/snapshot_qdrant.sh` — NEW
8. `scripts/backup_postgres.sh` — NEW
9. `scripts/bootstrap.sh` — NEW
10. `deploy/qdrant-rag.service` — NEW (systemd unit)
11. `deploy/nginx/qdrant_rag.conf.example` — NEW (HTTP + gRPC server blocks)
12. `RUNBOOK.md` — NEW
13. `.github/workflows/ci.yml` — NEW
14. `Makefile` — MODIFIED (add `snapshot`, `backup`, `load-test` targets + help block)
15. `docker-compose.yml` — MODIFIED (add `stop_grace_period: 30s` to `grpc`)
16. `.env.example` — MODIFIED (document new env vars: `BACKUP_DIR_QDRANT`, `BACKUP_DIR_POSTGRES`, `QDRANT_SNAPSHOT_KEEP`, `POSTGRES_BACKUP_KEEP`, `DEPLOY_USER`)
17. `README.md` — MODIFIED (mark Phase 8b complete; add "Deployment" section linking to RUNBOOK + bootstrap.sh)

### Tests modified (1)

18. `tests/test_observability.py` — UPDATE: assertions on access log line now check for `method`, `path`, `status_code`, `duration_ms` keys (which the ExtraAdder fix unblocks)

---

## Behavior — exact contracts

### Access log line (post-Phase-8b polish)

```json
{
  "event": "request_completed",
  "service": "qdrant_rag",
  "version": "0.1.0-dev",
  "level": "info",
  "logger": "apps.core.access",
  "timestamp": "2026-04-29T11:38:48.388721Z",
  "request_id": "...",
  "tenant_id": "test_t",
  "bot_id": "test_b",
  "method": "POST",
  "path": "/v1/tenants/test_t/bots/test_b/documents",
  "status_code": 201,
  "duration_ms": 1234.5,
  "phases": {"chunk": 12.4, "embed": 980.1, "upsert": 230.7}
}
```

### Counter behavior

- **Per HTTP request (non-excluded paths):** `http_requests_total{method=POST, endpoint=upload-document, status_code=201}` increments by 1; `http_request_duration_seconds{method=POST, endpoint=upload-document}` observes the duration.
- **Per gRPC RPC:** `grpc_requests_total{rpc=Search, status_code=OK}` increments; `grpc_request_duration_seconds{rpc=Search}` observes.
- **Per pipeline phase:** `pipeline_phase_duration_seconds{phase=embed}` observes per-phase duration; emitted from access log middleware once per request.
- **Per search response:** `search_threshold_used` set to current value; `search_results_count` observes `total_candidates`.
- **Per BGE-M3 load:** `embedder_loaded` set to 1 (sticky).

### bootstrap.sh flow

```
[bootstrap] preflight: docker installed, compose v2 installed
[bootstrap] deploy user: bol7 (from $SUDO_USER)
[bootstrap] adding bol7 to docker group
[bootstrap] copying .env.example → .env (.env not found)
[bootstrap] running make bge-download as bol7 (sg docker -c)
... (BGE-M3 download log) ...
[bootstrap] running make up
[bootstrap] waiting for healthz...
[bootstrap] healthz green: {"status": "ok", ...}
[bootstrap] DONE. Stack live on http://localhost:${HTTP_PORT:-8080}
```

### Makefile help additions

```
make snapshot           Take a Qdrant snapshot (all collections; rotation 7)
make backup             Take a Postgres backup (rotation 14)
make load-test          Run scripts/load_test.py against the local stack
```

---

## Acceptance criteria

1. **`/healthz` includes `X-Request-ID` in response** (Section 0 fix #1).
2. **Access log line includes `method` / `path` / `status_code` / `duration_ms` / `phases`** (Section 0 fix #2). Verified via `make logs | grep request_completed | head -1` showing all five keys.
3. **`/metrics` shows non-zero counter values after a few requests** (Section 0 fix #3). Specifically: after 3 HTTP requests + 1 search, `qdrant_rag_http_requests_total{...}` shows >0; `qdrant_rag_pipeline_phase_duration_seconds_count{phase=embed}` shows ≥1; `qdrant_rag_search_results_count_count` shows ≥1.
4. **`embedder_loaded` gauge flips to 1** after first embed call.
5. **gRPC counters increment** after a `grpcurl` Search call (verified by metrics scrape).
6. **`bash scripts/bootstrap.sh`** on a fresh Ubuntu 24.04 VM provisions a working stack from zero. (Acceptance verified manually; document in implementation report.)
7. **`bash scripts/bootstrap.sh`** is idempotent — running it on an already-bootstrapped host exits 0 with "already bootstrapped" message.
8. **`bash scripts/snapshot_qdrant.sh`** creates a snapshot in `$BACKUP_DIR_QDRANT/<timestamp>/` and rotates to keep last 7.
9. **`bash scripts/backup_postgres.sh`** creates a `pg_dump` file in `$BACKUP_DIR_POSTGRES/` and rotates to keep last 14.
10. **`make snapshot` and `make backup`** are wired and runnable.
11. **`deploy/qdrant-rag.service`** installs cleanly (`systemctl daemon-reload && systemctl enable qdrant-rag` succeed) and `systemctl start qdrant-rag` brings up the stack.
12. **`deploy/nginx/qdrant_rag.conf.example`** parses cleanly via `nginx -t -c <file>` (or equivalent dry-run check).
13. **`.github/workflows/ci.yml`** runs to completion in GitHub Actions: ruff check + format-check pass, migrations-check passes, pytest passes (excluding embedder tests). Required status check enabled on PRs.
14. **`docker-compose.yml`** has `stop_grace_period: 30s` on the `grpc` service.
15. **RUNBOOK.md exists** and contains all 10 documented sections (deploy / upgrade / rollback / restart / logs / metrics / restore-postgres / restore-qdrant / rotate-secrets / failure-modes).
16. **README.md** marks Phase 8b complete and adds a Deployment section pointing to RUNBOOK + bootstrap.sh.
17. **Phase 1-8a regression:** full host suite stays green or matches the documented host-equivalent state.
18. **Stack health:** `make rebuild && make ps && make health` all green after this phase ships.

---

## Common pitfalls

1. **Counter wiring causes test fixtures to fail.** Existing tests that hit views without setting up Prometheus may need adjustments. Use a clean test registry in test fixtures (`prometheus_client.CollectorRegistry()`) or accept the global default (which is what the current test_observability.py uses).

2. **Recorder helper called on excluded paths.** `record_http_request` should ONLY be called from inside `AccessLogMiddleware.__call__` AFTER the path-exclusion check. Putting it in a different middleware would re-introduce the exclusion list.

3. **`ExtraAdder` ordering.** Must come AFTER `add_log_level` (which expects a clean event_dict) and BEFORE `_request_context_processor` (which adds keys via `setdefault`). Wrong order = dropped fields or KeyError.

4. **bootstrap.sh assumes deb-family package manager.** Use `apt-get` for prereq install. Document "tested on Ubuntu 24.04 / Debian 12" — fedora/RHEL operators get a stub error message pointing to manual install.

5. **bootstrap.sh runs `make bge-download` synchronously.** Takes 5-15 minutes (~4.5 GB download). Show a progress banner; don't time out.

6. **bootstrap.sh on already-running stack tries to start it again.** Idempotent check: `docker compose ps -q web` returns a container ID → already up; skip `make up`.

7. **Snapshot script doesn't clean up failed snapshots.** Wrap in `set -e`; trap on failure; remove the partial snapshot dir before exiting.

8. **Postgres backup script omits `-Fc` (custom format).** Required for `pg_restore` compatibility. Don't use plain SQL dumps.

9. **systemd unit's `ExecStart` can't `cd` into the project dir.** Use `WorkingDirectory=` directive. Hardcode or template the project path; document at top of unit file.

10. **nginx config uses `proxy_pass` for gRPC.** WRONG — must use `grpc_pass`. The `grpc_pass grpc://...` directive is required; HTTP/2 is required.

11. **CI Postgres service container starts before the `wait-for-postgres` step.** Use `pg_isready` polling or rely on `services.postgres.options.healthcheck` in the YAML.

12. **CI cache key forgets to include `pyproject.toml`.** Cache invalidates only on `uv.lock` SHA; if pyproject changes without lock changes (unusual), cache hits stale artifacts. Belt-and-suspenders: include both files in cache key.

13. **`stop_grace_period` on Compose only affects `docker compose down`, not `docker compose restart`.** Document in RUNBOOK.

14. **RUNBOOK secret rotation forgets to mention `make rebuild` after `.env` changes.** `.env` is read at container startup. Without rebuild + restart, new value isn't loaded. Each rotation step must end with the rebuild + verify.

15. **Counter increments use `request.path` instead of URL pattern name.** `path` includes UUIDs, blowing cardinality. Use `request.resolver_match.url_name` (already specified in 8a; carry forward in 8b).

16. **The 8a polish breaks tests asserting on the OLD log line shape.** Specifically `tests/test_observability.py` may have weak assertions like `assert "method" in log_line` that are currently failing silently. Verify each test now asserts what 8b makes true.

17. **CI doesn't authenticate to Qdrant.** Phase 7 added `QDRANT__SERVICE__API_KEY` requirement; CI must set this env var when starting the Qdrant service container.

18. **Bootstrap requires HuggingFace network access during BGE download.** Sandbox/airgapped environments fail. Document as a precondition, not a script bug.

---

## Out of scope (deferred to post-v1 — never in 8b)

- Container registry + tagged images (Phase 9+ enables proper rollback)
- Auth (post-v1; URL structure already prepared)
- TLS termination *inside* the service (always nginx upstream)
- Async ingestion via Celery (wired-but-unused stays so)
- Redis cache layer (v3)
- Audit log table (v3)
- Quantization (v4)
- Per-tenant config (v5)
- Multi-host orchestration (k8s/Nomad/ECS)
- Auto-scaling, blue/green deploys
- Grafana dashboard JSON files (RUNBOOK documents where to find metrics; dashboards are env-specific)
- Alertmanager rule files (RUNBOOK lists recommended alerts; rules live in your Prometheus)
- Database connection pool tuning beyond Phase 1's `CONN_MAX_AGE=60`
- Hierarchical chunking (deferred per memory; needs scraping standardization first)

---

## Success looks like

- An operator with no prior knowledge of this codebase reads `RUNBOOK.md`, runs `bash scripts/bootstrap.sh` on a fresh Ubuntu 24.04 VM, and has a working stack 15-25 minutes later.
- `curl http://localhost:8080/metrics` returns counters with non-zero values.
- One JSON `request_completed` line per non-excluded HTTP request, containing all 5 documented kwargs + ContextVar enrichment.
- `make snapshot` produces a working Qdrant snapshot; `make backup` produces a `pg_restore`-compatible Postgres dump.
- A PR opened against main runs CI to completion in <5 minutes; required status check blocks merge until green.
- `kill -TERM <grpc-pid>` drains in-flight requests and exits within ~10 seconds (Compose's 30s grace gives buffer).
- v1 ships.
