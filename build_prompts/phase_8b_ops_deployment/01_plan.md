# Phase 8b — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not modify any file.**

---

## Required reading (in this order)

1. `README.md` — current API surface, status table, Phase 8a complete.
2. `build_prompts/phase_8b_ops_deployment/spec.md` — full Phase 8b spec. **Source of truth. Read twice.**
3. `build_prompts/phase_8a_code_hardening/spec.md` — Phase 8a contract; Section 0 polish closes deviations against this.
4. `build_prompts/phase_8a_code_hardening/implementation_report.md` — Phase 8a outcomes including the three documented deviations.
5. `apps/core/middleware.py`, `apps/core/logging.py`, `apps/core/metrics.py` — current state of observability infrastructure.
6. `apps/grpc_service/handler.py` — current handler (no metric wiring).
7. `apps/ingestion/embedder.py`, `apps/ingestion/pipeline.py`, `apps/qdrant_core/search.py` — recording sites.
8. `Makefile`, `docker-compose.yml`, `.env`, `.env.example` — operational surface.
9. `tests/test_observability.py` — current observability test assertions.

If `phase_8b_ops_deployment/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_8b_ops_deployment/plan.md
```

---

## What the plan must contain

### 1. Plan summary
4–6 sentences. What's being added (8a polish + ops scripts + RUNBOOK + CI + nginx + bootstrap)? What's the riskiest part (bootstrap idempotency, CI service-container coordination, ExtraAdder ordering)? How does the build verify itself?

### 2. Build order & dependency graph

Phase 8b modifies 7 files and creates 8. Order is critical: **Section 0 (polish) ships first** so verification of new ops scripts sees corrected behavior:

**Section 0 — 8a polish (do FIRST):**
- `apps/core/logging.py` — add `ExtraAdder` to both processor chains
- `apps/core/middleware.py` — narrow RequestID exclusion to `/static/` only; integrate counter recorder calls into AccessLogMiddleware
- `apps/core/metrics_recorders.py` (NEW) — three recorder helpers
- `apps/grpc_service/handler.py` — `_record_metrics` decorator on RPCs
- `apps/ingestion/embedder.py` — `embedder_loaded.set(1)` post-load
- `apps/qdrant_core/search.py` — `search_threshold_used` gauge + `search_results_count` histogram on success
- `tests/test_observability.py` — update assertions (now checks for `method`/`path`/`status_code`/`duration_ms`)

**Section 1 — Operational artifacts:**
- `docker-compose.yml` — add `stop_grace_period: 30s` to `grpc` (single line; trivial; do early)
- `Makefile` — three new targets (`snapshot`, `backup`, `load-test`); modify help section
- `.env.example` — document new env vars
- `scripts/snapshot_qdrant.sh` — NEW
- `scripts/backup_postgres.sh` — NEW
- `scripts/bootstrap.sh` — NEW
- `deploy/qdrant-rag.service` — NEW
- `deploy/nginx/qdrant_rag.conf.example` — NEW
- `RUNBOOK.md` — NEW
- `.github/workflows/ci.yml` — NEW
- `README.md` — modify (mark 8b complete; add Deployment section)

Stack rebuild + smoke after Section 0 changes settle (verify metrics increment, access log enriched).
Final regression after Section 1 changes settle (verify Makefile targets, scripts execute).

### 3. Build steps (sequenced)

15–20 numbered steps. Each: goal · files · verification · rollback.

Critical sequencing:
- `apps/core/logging.py` `ExtraAdder` BEFORE `tests/test_observability.py` updates (otherwise tests fail).
- `apps/core/metrics_recorders.py` BEFORE the middleware/handler/embedder/search edits that import from it.
- All Section 0 fixes BEFORE rebuild #1 (verify polish landed).
- `scripts/*.sh` are independent of each other; any order.
- `bootstrap.sh` is the most complex script — schedule its step with extra time budget.
- `RUNBOOK.md` written after scripts settle (so command examples reflect what actually works).
- `Makefile` updated AFTER scripts exist (targets reference them).
- CI workflow added LAST (least likely to need reverification on local).
- `README.md` updated last (mark phase complete).

### 4. Risk register

Reference all 18 spec.md "Common pitfalls" with concrete preventative steps. Especially:

- **Counter wiring causes test fixture failures.** Check that `tests/test_observability.py` (and any other test that hits views) doesn't break when counters now increment. Use `prometheus_client.REGISTRY.unregister(...)` between tests if needed; or accept incrementing-but-untested counter values.
- **`ExtraAdder` ordering.** Must follow `add_log_level`, precede `_request_context_processor`. Both processor chains.
- **bootstrap.sh non-idempotent first run failure.** Trap errors; if first run fails midway, re-running must pick up where it left off (or cleanly redo from scratch).
- **bootstrap.sh BGE download timing.** ~5-15 min synchronous wait; show progress; don't time out the script.
- **Snapshot script partial-failure cleanup.** `set -e` + trap removes partial snapshot dir.
- **Postgres backup uses `-Fc`** (custom format) for `pg_restore` compatibility.
- **systemd `WorkingDirectory=`** must point to project root (templated; document at top).
- **nginx uses `grpc_pass`** for gRPC, not `proxy_pass`.
- **CI Postgres healthcheck** before tests run.
- **CI Qdrant API key** matches local `.env` test value.
- **stop_grace_period only affects `down`** not `restart`. Document in RUNBOOK.
- **RUNBOOK secret rotation always ends with rebuild + verify.**
- **HTTP request counter labels use `url_name`** not `request.path`.

### 5. Verification checkpoints

12–18 with exact commands and expected outcomes:

- After `apps/core/logging.py`: `make run python -c "from apps.core.logging import _SHARED_PROCESSORS; assert any('ExtraAdder' in repr(p) for p in _SHARED_PROCESSORS)"`.
- After `apps/core/metrics_recorders.py`: import smoke + `make run python -c "from apps.core.metrics_recorders import record_http_request, record_grpc_request, record_pipeline_phase; print('ok')"`.
- After `middleware.py` polish: `manage.py check` clean; `curl -i http://localhost:8080/healthz` shows `X-Request-ID`.
- After `handler.py` decorator: `manage.py check` clean.
- After `embedder.py`: `make run python -c "from apps.ingestion.embedder import warmup; warmup(); from apps.core.metrics import embedder_loaded; print(embedder_loaded._value.get())"` prints 1.
- After `search.py` recorder: `make run pytest tests/test_search_query.py -v` stays green.
- After `tests/test_observability.py` update: `make run pytest tests/test_observability.py -v` all green.
- Stack rebuild #1 (post-Section 0): `make rebuild && make ps && make health`.
- Manual: 3 HTTP requests + 1 search → `curl -sS http://localhost:8080/metrics | grep -E 'http_requests_total|search_results_count_count'` shows non-zero.
- Manual: `curl http://localhost:8080/healthz` → response includes `X-Request-ID`; access log line has all 5 kwargs.
- After `docker-compose.yml`: `docker compose config | grep -A1 stop_grace_period` shows `30s`.
- After `scripts/snapshot_qdrant.sh`: `bash scripts/snapshot_qdrant.sh test_t__b_test_b` succeeds; `ls $BACKUP_DIR_QDRANT/` shows the snapshot.
- After `scripts/backup_postgres.sh`: `bash scripts/backup_postgres.sh` succeeds; `ls $BACKUP_DIR_POSTGRES/` shows the dump.
- After `scripts/bootstrap.sh`: idempotent re-run on already-bootstrapped host exits 0 with "already bootstrapped" message.
- After `Makefile`: `make snapshot && make backup` succeed.
- After `deploy/qdrant-rag.service`: `systemd-analyze verify deploy/qdrant-rag.service` succeeds.
- After `deploy/nginx/qdrant_rag.conf.example`: `nginx -t -c <file>` (or equivalent dry-run) succeeds.
- After `.github/workflows/ci.yml`: actionlint or yaml syntax check passes.
- After `RUNBOOK.md`: 10 sections present; each has "verify success by:" tail.
- After `README.md`: status table shows Phase 8b COMPLETE.
- Phase 1-8a regression: `make run pytest -v` keeps all prior tests green.

### 6. Spec ambiguities & open questions

4-6 entries:

- **`tests/test_observability.py` update scope.** Spec says update assertions to check for new fields. Plan should clarify: does this mean adding new positive assertions, or modifying existing ones? Recommend: replace any "absence" assertions with positive checks; keep existing positive checks.
- **bootstrap.sh package manager assumption.** Spec mentions deb-family. Plan should commit on what happens for non-deb hosts: stub error message + manual install instructions.
- **Snapshot rotation when zero existing snapshots.** Edge case: rotation logic on first run with no prior snapshots. Plan should commit: skip rotation step if count <= keep limit.
- **Counter wiring in views vs middleware.** Plan should explicitly commit: HTTP counters wired in `AccessLogMiddleware`; pipeline phase counters wired in same middleware (iterating timer dict); gRPC counters via decorator on handler methods. NOT in views.
- **CI matrix.** Spec implies single Python version (3.13). Plan should confirm: no matrix; single Python 3.13 run.
- **`embedder_loaded` gauge in test fixture.** Tests mock the embedder; the gauge stays at 0 in tests. Plan should commit on whether tests assert on this (recommend: don't, since mocked).

### 7. Files deliberately NOT created / NOT modified

Echo spec.md "Out of scope (post-v1)" + the don't-touch list (everything not in the 17-file modification set).

### 8. Acceptance-criteria mapping

For all 18 acceptance criteria from spec.md: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Section 0 verification
make run python manage.py check
make run pytest tests/test_observability.py -v

# Stack
make rebuild
make ps && make health

# Section 0 smoke
curl -i http://localhost:8080/healthz | grep X-Request-ID
curl -sS http://localhost:8080/metrics | grep -E 'http_requests_total|search_results_count_count'
make logs 2>&1 | grep request_completed | head -3

# Section 1 verification
bash scripts/snapshot_qdrant.sh
bash scripts/backup_postgres.sh
bash scripts/bootstrap.sh   # idempotent re-run on already-bootstrapped host
make snapshot && make backup
systemd-analyze verify deploy/qdrant-rag.service
nginx -t -c $(pwd)/deploy/nginx/qdrant_rag.conf.example   # may need PWD-substituted

# Full regression
make run pytest -v
```

### 10. Estimated effort

Per step. Phase 8b is moderately large: ~3-5 hours of agent work. Section 0 is ~30 min; Section 1 is the bulk (RUNBOOK alone is ~1 hour).

---

## Output format

Single markdown file at `build_prompts/phase_8b_ops_deployment/plan.md`. 400–700 lines.

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Total line count.
3. 5-bullet summary of key sequencing decisions (especially: Section 0 first, scripts before RUNBOOK, CI last).
4. Spec ambiguities flagged in section 6 (titles only).

Then **stop**.
