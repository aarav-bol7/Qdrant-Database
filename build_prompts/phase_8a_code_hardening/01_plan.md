# Phase 8a — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not modify any file.**

---

## Required reading (in this order)

1. `README.md` — current API surface, architecture, and Phase 7.6 status.
2. `build_prompts/phase_8a_code_hardening/spec.md` — full Phase 8a spec. **Source of truth. Read twice.**
3. `build_prompts/phase_7_search_grpc/spec.md` — gRPC server contract; this phase adds shutdown handler + reflection toggle.
4. `build_prompts/phase_7_search_grpc/implementation_report.md` — Phase 7 outcomes (RRF emulation via duplicated dense Prefetch — the runtime smoke test exercises this).
5. `build_prompts/phase_7_5_api_cleanup/spec.md` — Chunk proto trim + reserved field numbers; the backward-compat test verifies wire compat with old-schema payloads.
6. `build_prompts/phase_5b_upload_idempotency/spec.md` — pipeline phases the access log will time.
7. `apps/core/logging.py` — current structlog config; this phase extends it.
8. `apps/grpc_service/server.py` — current gRPC bootstrap.
9. `apps/ingestion/pipeline.py` — pipeline timing points.
10. `apps/qdrant_core/search.py` — search function the runtime tests target.
11. `config/settings.py`, `config/urls.py` — Django wiring points.
12. `pyproject.toml` — dependency manifest.

If `phase_8a_code_hardening/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_8a_code_hardening/plan.md
```

---

## What the plan must contain

### 1. Plan summary
4–6 sentences. What's being added (observability + RRF/backward-compat tests + gRPC shutdown + load script)? What's the riskiest part (ContextVar leakage, signal-handler thread safety, structlog processor ordering)? How does the build verify itself?

### 2. Build order & dependency graph
Phase 8a creates 8 files and modifies 5. Order:

- `pyproject.toml` first — add `prometheus-client` to main deps and `grpcio-reflection` to dev deps; run `uv lock` to refresh `uv.lock` so the rebuild picks them up.
- `apps/core/timing.py` — small standalone helper (ContextVar dict + `timer()` context manager). No deps on other new code.
- `apps/core/middleware.py` — depends on timing.py. Ships `RequestIDMiddleware` + `AccessLogMiddleware`.
- `apps/core/logging.py` — extend with the request-context-enrichment structlog processor. Depends on middleware's ContextVar definitions.
- `apps/core/metrics.py` — Prometheus registry + view. No deps on other new code.
- `config/settings.py` — wire in middleware (`RequestID` first, `AccessLog` last) + register the new structlog processor.
- `config/urls.py` — add `/metrics` route.
- `apps/ingestion/pipeline.py` — wrap pipeline phases in `timer()`. Depends on timing.py existing.
- `apps/grpc_service/server.py` — add reflection toggle + signal handler. Independent of other new code.
- `.env.example` — document new env vars (`GRPC_ENABLE_REFLECTION`, `GRPC_SHUTDOWN_GRACE_SECONDS`).
- Tests added LAST: `test_observability.py`, `test_search_runtime.py`, `test_grpc_shutdown.py`.
- `scripts/load_test.py` — standalone. Order doesn't matter relative to tests.
- Stack rebuild + smoke after all source changes.

### 3. Build steps (sequenced)
12–15 numbered steps. Each: goal · files · verification · rollback.

Critical sequencing:
- `pyproject.toml` + `uv lock` BEFORE the rebuild that will install `prometheus-client` and `grpcio-reflection`.
- Middleware module's ContextVars must be defined BEFORE the structlog processor that reads them imports them.
- Pipeline phase timer wrap goes IN THE SAME COMMIT as `timing.py` to avoid a broken intermediate state.
- gRPC server's signal-handler registration happens in the main thread (server bootstrap path); shutdown test verifies this.
- Tests added after all source changes settle.
- Stack rebuild AFTER all source changes.

### 4. Risk register
Reference all 13 spec.md "Common pitfalls" with concrete preventative steps. Especially:

- **ContextVar leakage between requests** — middleware must use `var.reset(token)` in finally, not just `var.set(...)`.
- **`/metrics` self-feedback loop** — metrics view must early-exit before the request_completed log line and before incrementing `qdrant_rag_http_requests_total` for itself.
- **structlog processor ordering** — request-context-enrichment processor must run BEFORE the JSON renderer.
- **gRPC reflection import safety** — wrap reflection import in `try/except ImportError` so prod (without grpcio-reflection) doesn't crash on startup.
- **Signal handler in main thread** — `signal.signal(SIGTERM, handler)` MUST be called from the main thread; don't spawn the gRPC server bootstrap in a sub-thread.
- **Backward-compat test pollutes search collection** — use unique tenant/bot pair for the fixture chunk.
- **Pipeline timer leaks state on exception** — `try/finally` records duration even when phase fails.

### 5. Verification checkpoints
10–14 with exact commands and expected outcomes:

- After `pyproject.toml` edit: `uv lock` succeeds; `uv.lock` shows `prometheus-client` and `grpcio-reflection`.
- After `apps/core/timing.py`: `make run python -c "from apps.core.timing import timer; print('ok')"`.
- After `apps/core/middleware.py`: import smoke + `manage.py check` clean.
- After `apps/core/metrics.py`: import smoke + `make run python -c "from apps.core.metrics import REGISTRY, http_requests_total; print(http_requests_total)"`.
- After `apps/core/logging.py` extension: `manage.py check` clean.
- After `config/settings.py`: `manage.py check` clean.
- After `config/urls.py`: `make run python manage.py shell -c "from django.urls import reverse; print(reverse('metrics'))"` prints `/metrics`.
- After `apps/ingestion/pipeline.py`: `manage.py check` clean.
- After `apps/grpc_service/server.py`: `manage.py check` clean.
- Stack rebuild: `make rebuild && make ps && make health` green.
- Manual `curl http://localhost:8080/metrics` returns Prometheus exposition (200, text/plain, content includes counter/histogram metric names).
- Manual `curl -i http://localhost:8080/healthz` returns `X-Request-ID` header.
- Manual log inspection: `make logs | grep request_completed | head -5` shows JSON access-log lines.
- gRPC reflection smoke: with `GRPC_ENABLE_REFLECTION=True` set in `.env`, restart, then `grpcurl -plaintext localhost:50051 list` returns the service.
- Tests in container: `make run pytest tests/test_observability.py tests/test_search_runtime.py tests/test_grpc_shutdown.py -v` all green.
- Phase 1-7.6 regression: `make run pytest -v` keeps all prior tests green.
- Load smoke: `python scripts/load_test.py --uploads 10 --searches 100` completes; reports baseline numbers.

### 6. Spec ambiguities & open questions
4-6 entries:

- **`tenant_id` / `bot_id` / `doc_id` ContextVars.** Spec says request-context enrichment includes these. Where do they get set? Recommendation: a small helper called from upload/delete/search views right after path parameters are bound. Plan should commit on this.
- **Embedder loaded gauge.** Spec lists `qdrant_rag_embedder_loaded`. Where does it get set to 1? Recommendation: in `apps.ingestion.embedder._get_model()` post-load, OR via a process-startup probe. Plan should pick.
- **Backward-compat test fixture lifetime.** Should the manually-upserted point be cleaned up after the test? Recommendation: use pytest's `tmp_path` analog for Qdrant — create on a unique collection name, drop on teardown.
- **Load test target URL.** Hard-coded `localhost:8080` or env-configurable? Recommend env var `QDRANT_RAG_URL` with localhost default.
- **gRPC shutdown opt-in subprocess test cross-platform.** Spec marks macOS as flaky. Plan should include `pytest.mark.skipif(sys.platform == "darwin")` or skip altogether unless `RUN_SIGTERM_TEST=1`.
- **Per-request `phases` dict population for non-pipeline endpoints.** Spec says "empty for non-pipeline endpoints." But `/healthz` and admin pages won't have a `tenant_id` either. The access log line for `/healthz` should NOT include `tenant_id`/`bot_id`/`doc_id` keys at all (vs. setting them to None). Plan should commit.

### 7. Files deliberately NOT created / NOT modified
Echo spec.md "Out of scope (Phase 8b)" + "Out of scope (post-v1)" + the don't-touch list (everything not in the 12-file modification set + 1 dependency change).

### 8. Acceptance-criteria mapping
For all 14 acceptance criteria from spec.md: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Dependency
uv lock
uv sync --frozen

# Standard
make run python manage.py check
make run pytest -v
make run pytest tests/test_observability.py tests/test_search_runtime.py tests/test_grpc_shutdown.py -v

# Stack
make rebuild
make ps
make health

# Smoke
curl -i http://localhost:8080/healthz | head -10                # X-Request-ID present
curl -sS http://localhost:8080/metrics | grep qdrant_rag_       # metrics enumerated
make logs 2>&1 | grep request_completed | head -3                # access log lines

# gRPC reflection
GRPC_ENABLE_REFLECTION=True make rebuild
grpcurl -plaintext localhost:50051 list

# Graceful shutdown
docker exec qdrant_rag_grpc kill -TERM 1
docker compose logs grpc | tail -20

# Load smoke
python scripts/load_test.py --uploads 10 --searches 100
```

### 10. Estimated effort
Per step. Phase 8a is the largest pre-Phase-8b phase: ~2-4 hours of agent work, ~300-500 lines of net new code + ~200-300 lines of tests + 1 standalone load script.

---

## Output format

Single markdown file at `build_prompts/phase_8a_code_hardening/plan.md`. 350–600 lines.

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Total line count.
3. 5-bullet summary of key sequencing decisions (especially: ContextVar reset/leakage, structlog processor ordering, signal-handler thread, pipeline phase timer wrap, dependency lock).
4. Spec ambiguities flagged in section 6 (titles only).

Then **stop**.
