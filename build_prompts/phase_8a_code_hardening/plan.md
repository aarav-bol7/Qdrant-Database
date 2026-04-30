# Phase 8a — Implementation Plan (revised)

> Produced by Prompt 1 (PLAN), revised by Prompt 2 (REVIEW). Inputs: Phase 8a spec.md, Phases 7/7.5/7.6 implementation reports, current state of source files, plan_review.md.

---

## 0. Revision notes

This plan is revision 2. Findings from `plan_review.md`:

- **F1 [major]:** Compose `stop_grace_period` vs `GRPC_SHUTDOWN_GRACE_SECONDS` boundary — plan §3.9 keeps spec default 10s; R9 documents that Phase 8b owns the compose tweak (raise to 30s).
- **F2 [major]:** AccessLogMiddleware on exception path — §3.3 revised with explicit try/except: `status_code = 500` on exception; finally block always emits the log.
- **F3 [major]:** `apps/documents/views.py` modification (one-line `set_request_context` calls) is wiring, not algorithm — §7 documents as deviation 1; implementation report will mention.
- **F4 [major]:** Backward-compat test fixture — §3.12 revised: collection created via `create_collection_for_bot(...)` (real vector schema); only the upserted POINT's payload carries deprecated keys.
- **F5-F16 minors** documented inline.

Zero critical findings. Plan proceeds.

---

## 1. Plan summary

Phase 8a turns the functionally-complete service into a production-operable one: a `/metrics` Prometheus endpoint with bounded-cardinality labels (no tenant/bot in label keys); a `RequestIDMiddleware` + `AccessLogMiddleware` pair that uses `contextvars.ContextVar` to thread `request_id`/`tenant_id`/`bot_id`/`doc_id` through the whole request lifecycle and emits exactly one `request_completed` JSON log line per request with phase-timing breakdown; a small `timer(phase)` context manager the upload pipeline wraps each stage in; a `_request_context_processor` for structlog that auto-enriches every log line with the current ContextVars; an env-flagged gRPC reflection toggle and a SIGTERM-aware graceful shutdown handler installed from the main thread; deterministic RRF + backward-compat smoke tests; and an opt-in `scripts/load_test.py` that drives 10 concurrent uploads + 100 concurrent searches and reports p50/p95/p99 baselines.

The riskiest pieces are: (a) **ContextVar leakage** between WSGI requests — the middleware MUST capture the token from `var.set(value)` and call `var.reset(token)` in a finally block; (b) **structlog processor ordering** — the new context-enrichment processor must run BEFORE the JSON renderer or it adds nothing to the output; (c) **signal handler thread placement** — Python signals only deliver to the main thread, so the handler must be installed before `server.wait_for_termination()` and not from any sub-thread; (d) **`/metrics` self-feedback loop** — the metrics view must NOT increment `qdrant_rag_http_requests_total` for itself, and must be excluded from `RequestIDMiddleware` + `AccessLogMiddleware` via path-prefix early-exit. The build verifies itself via: import smokes after each step, `manage.py check`, focused pytest of the 3 new test files, full Phase 1-7.6 regression, manual `curl /metrics` + `curl /healthz` (X-Request-ID echo), `grpcurl list` with reflection on/off, `kill -TERM` shutdown smoke, and the load script.

---

## 2. Build order & dependency graph

| # | Artifact | Depends on | Why |
|---|---|---|---|
| 1 | `pyproject.toml` + `uv lock` | — | Add `prometheus-client>=0.20` (main) + `grpcio-reflection>=1.60` (dev). Lockfile must regenerate before image rebuild. |
| 2 | `apps/core/timing.py` | — | ContextVar dict + `timer(phase)` ctx mgr. Standalone; no deps. |
| 3 | `apps/core/middleware.py` | 2 | Defines the four ContextVars (`_request_id_var`, `_tenant_id_var`, `_bot_id_var`, `_doc_id_var`) + the two middleware classes + `set_request_context()` helper used by views/pipeline. |
| 4 | `apps/core/logging.py` | 3 (imports the ContextVars) | Add `_request_context_processor` that reads the four ContextVars and merges into event_dict BEFORE the JSON renderer. |
| 5 | `apps/core/metrics.py` | — (independent, but deps on prometheus-client from step 1) | 8 metrics + `metrics_view`. |
| 6 | `config/settings.py` | 3, 4 | Wire middleware (RequestIDMiddleware FIRST, AccessLogMiddleware LAST) + structlog processor (BEFORE JSON renderer). |
| 7 | `config/urls.py` | 5 | Add `path("metrics", apps.core.metrics.metrics_view, name="metrics")`. |
| 8 | `apps/ingestion/pipeline.py` | 2, 3 | Wrap phases in `timer(...)`; call `set_request_context(...)` early. |
| 9 | `apps/grpc_service/server.py` | 1 (grpcio-reflection) | Add reflection toggle (try/except ImportError) + main-thread SIGTERM handler. |
| 10 | `.env.example` | 9 | Document `GRPC_ENABLE_REFLECTION=False` + `GRPC_SHUTDOWN_GRACE_SECONDS=10`. |
| 11 | `tests/test_observability.py` | 3, 4, 5, 6, 7 | Header echo/generate; `/metrics` exposition; exclusions; ContextVar isolation; structlog enrichment. |
| 12 | `tests/test_search_runtime.py` | (apps/qdrant_core/search.py — unchanged) | RRF smoke + backward-compat regression. |
| 13 | `tests/test_grpc_shutdown.py` | 9 | Default + opt-in subprocess. |
| 14 | `scripts/load_test.py` | 5 (httpx already in dev deps) | Async load script. |
| 15 | Stack rebuild + smoke | 1-14 | `make rebuild` so the new dep is baked into the image. |

Notes:
- Steps 2 and 5 can run in parallel; both are dep-free. Step 3 must wait for step 2 (imports `timer`).
- Step 4 imports the four ContextVars from step 3 — same module, same commit.
- Step 8 (pipeline timing) and step 3 (middleware) MUST land in the same logical step or the pipeline imports a not-yet-existing `timer` symbol. Plan groups these via the dep table.
- Step 9 requires `grpcio-reflection` available (pip-resolvable) but the runtime import is wrapped in try/except, so even without the dep the server still starts (with reflection unavailable).
- Step 15's `make rebuild` (NOT `make wipe`) preserves bge_cache + Postgres data per spec hard constraint #20.

---

## 3. Build steps (sequenced)

### Step 3.1 — `pyproject.toml` + `uv lock`

- **Goal:** add deps; regenerate lockfile.
- **Diff:**
  - Main `[project].dependencies`: append `"prometheus-client>=0.20",`.
  - `[dependency-groups].dev`: append `"grpcio-reflection>=1.60",`.
- **Command:** `uv lock`.
- **Verification:** `grep -E "prometheus-client|grpcio-reflection" uv.lock` returns hits; `uv run python -c "import prometheus_client; print(prometheus_client.__version__)"` prints version.
- **Rollback:** revert pyproject.toml + `uv lock`.
- **Estimated effort:** 5 min.

### Step 3.2 — `apps/core/timing.py`

- **Goal:** small standalone helper.
- **Content (~25 lines):**
  ```python
  from __future__ import annotations
  import contextlib
  import contextvars
  import time
  from typing import Iterator

  _phase_durations_var: contextvars.ContextVar[dict[str, float] | None] = (
      contextvars.ContextVar("phase_durations", default=None)
  )

  def reset_phase_durations() -> dict[str, float]:
      """Initialize a fresh per-request phase-durations dict; returns it."""
      d: dict[str, float] = {}
      _phase_durations_var.set(d)
      return d

  def get_phase_durations() -> dict[str, float]:
      """Return current dict or a fresh empty one if no request scope."""
      d = _phase_durations_var.get()
      return d if d is not None else {}

  @contextlib.contextmanager
  def timer(phase: str) -> Iterator[None]:
      """Record elapsed-ms into the per-request phase-durations dict.
      No-op if no request scope is active.
      """
      d = _phase_durations_var.get()
      started = time.monotonic()
      try:
          yield
      finally:
          if d is not None:
              d[phase] = (time.monotonic() - started) * 1000.0
  ```
- **Verification:** `uv run python -c "from apps.core.timing import timer, reset_phase_durations, get_phase_durations; d = reset_phase_durations(); import time; t=timer('x'); t.__enter__(); time.sleep(0.001); t.__exit__(None,None,None); print(d); assert 'x' in d"`.
- **Rollback:** `rm apps/core/timing.py`.
- **Estimated effort:** 10 min.

### Step 3.3 — `apps/core/middleware.py`

- **Goal:** the four ContextVars + RequestIDMiddleware + AccessLogMiddleware + `set_request_context()` helper.
- **Content (~110 lines):**
  ```python
  import logging
  import time
  import uuid
  from contextvars import ContextVar

  from apps.core.timing import get_phase_durations, reset_phase_durations

  logger = logging.getLogger("apps.core.access")

  _MAX_REQUEST_ID_LEN = 100
  _ACCESS_LOG_EXCLUDED_PREFIXES = ("/metrics", "/healthz", "/static/", "/admin/")

  _request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
  _tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
  _bot_id_var: ContextVar[str | None] = ContextVar("bot_id", default=None)
  _doc_id_var: ContextVar[str | None] = ContextVar("doc_id", default=None)


  def set_request_context(*, tenant_id=None, bot_id=None, doc_id=None) -> None:
      if tenant_id is not None: _tenant_id_var.set(tenant_id)
      if bot_id is not None:    _bot_id_var.set(bot_id)
      if doc_id is not None:    _doc_id_var.set(doc_id)


  class RequestIDMiddleware:
      def __init__(self, get_response):
          self.get_response = get_response

      def __call__(self, request):
          if request.path.startswith(_ACCESS_LOG_EXCLUDED_PREFIXES):
              return self.get_response(request)
          incoming = (request.headers.get("X-Request-ID") or "").strip()[:_MAX_REQUEST_ID_LEN]
          rid = incoming or str(uuid.uuid4())
          rid_token = _request_id_var.set(rid)
          tenant_token = _tenant_id_var.set(None)
          bot_token = _bot_id_var.set(None)
          doc_token = _doc_id_var.set(None)
          try:
              response = self.get_response(request)
              response["X-Request-ID"] = rid
              return response
          finally:
              _request_id_var.reset(rid_token)
              _tenant_id_var.reset(tenant_token)
              _bot_id_var.reset(bot_token)
              _doc_id_var.reset(doc_token)


  class AccessLogMiddleware:
      def __init__(self, get_response):
          self.get_response = get_response

      def __call__(self, request):
          if request.path.startswith(_ACCESS_LOG_EXCLUDED_PREFIXES):
              return self.get_response(request)
          phases = reset_phase_durations()
          started = time.monotonic()
          status_code = 500
          response = None
          try:
              response = self.get_response(request)
              status_code = response.status_code
              return response
          except Exception:
              status_code = 500
              raise
          finally:
              duration_ms = (time.monotonic() - started) * 1000.0
              extra = {
                  "method": request.method,
                  "path": request.path,
                  "status_code": status_code,
                  "duration_ms": round(duration_ms, 2),
                  "phases": phases,
              }
              tid = _tenant_id_var.get()
              bid = _bot_id_var.get()
              did = _doc_id_var.get()
              if tid: extra["tenant_id"] = tid
              if bid: extra["bot_id"] = bid
              if did: extra["doc_id"] = did
              logger.info("request_completed", extra=extra)
  ```
- **Verification:** import smoke + `manage.py check`.
- **Rollback:** `rm apps/core/middleware.py`.
- **Estimated effort:** 30 min.

### Step 3.4 — `apps/core/logging.py` extension

- **Goal:** add `_request_context_processor` BEFORE the JSON renderer.
- **Diff:** insert function + add to processor lists in BOTH `_SHARED_PROCESSORS` (foreign_pre_chain) AND inside `structlog.configure(processors=[...])`. Place after `merge_contextvars`, before `ProcessorFormatter.wrap_for_formatter`.
  ```python
  def _request_context_processor(logger, name, event_dict):
      """Enrich log events with request_id/tenant_id/bot_id/doc_id from ContextVars."""
      from apps.core.middleware import (
          _request_id_var, _tenant_id_var, _bot_id_var, _doc_id_var,
      )
      for key, var in (
          ("request_id", _request_id_var),
          ("tenant_id", _tenant_id_var),
          ("bot_id", _bot_id_var),
          ("doc_id", _doc_id_var),
      ):
          val = var.get()
          if val is not None:
              event_dict.setdefault(key, val)
      return event_dict
  ```
- **Why import inside the function:** logging.py is imported VERY early during settings load; middleware.py imports `apps.core.timing`. A top-level import would create a circular-ish chain. Lazy-import keeps the module-load order clean.
- **Verification:** `uv run python -c "from apps.core.logging import configure_logging, _request_context_processor; print('ok')"`.
- **Rollback:** restore the prior `logging.py`.
- **Estimated effort:** 15 min.

### Step 3.5 — `apps/core/metrics.py`

- **Goal:** 8 metrics + view per spec table.
- **Content (~75 lines):** `from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST`. Use the default REGISTRY (singleton); module-level metric definitions. The view: `def metrics_view(request) -> HttpResponse: return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)`.
  - Helpers: `record_http_request(endpoint, method, status_code, duration_seconds)`, `record_pipeline_phase(phase, seconds)`, `record_grpc(rpc, status_code, duration_seconds)`, `record_search_results(count, threshold)`, `set_embedder_loaded(loaded: bool)`. Helpers exist so callers don't have to know metric names.
  - Metric names from spec table verbatim.
- **Verification:** `uv run python -c "from apps.core.metrics import metrics_view; print('ok')"`.
- **Rollback:** `rm apps/core/metrics.py`.
- **Estimated effort:** 30 min.

### Step 3.6 — `config/settings.py` MIDDLEWARE wiring + logging.py processor

- **Goal:** plug middleware (RequestID FIRST after Security, AccessLog LAST) and the new structlog processor.
- **Diff:**
  - In `MIDDLEWARE` list: insert `"apps.core.middleware.RequestIDMiddleware"` after `SecurityMiddleware`; insert `"apps.core.middleware.AccessLogMiddleware"` last (after WhiteNoise / any other Django middleware).
  - In `structlog.configure(processors=[...])`: append `_request_context_processor` BEFORE `ProcessorFormatter.wrap_for_formatter`. Same for `_SHARED_PROCESSORS` (used by `foreign_pre_chain`).
- **Verification:** `uv run python manage.py check` clean; import settings → MIDDLEWARE list contains both classes in correct order.
- **Rollback:** revert settings.py.
- **Estimated effort:** 10 min.

### Step 3.7 — `config/urls.py` /metrics route

- **Goal:** add `path("metrics", metrics_view, name="metrics")`.
- **Diff:** import `apps.core.metrics.metrics_view`; add to `urlpatterns` BEFORE the `v1/` include (avoid catch-all). Comment that nginx (Phase 8b) will scope this to internal IPs at the edge.
- **Verification:** `uv run python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); import django; django.setup(); from django.urls import reverse; print(reverse('metrics'))"` → `/metrics`.
- **Rollback:** remove the path.
- **Estimated effort:** 5 min.

### Step 3.8 — `apps/ingestion/pipeline.py` phase timer wrap

- **Goal:** `with timer("phase"): ...` around each pipeline stage; `set_request_context(...)` at top.
- **Phases to wrap:** `dedup_check` (the existing-doc + cross-doc dedup queries), `get_or_create_collection`, `chunk` (the for-loop over items_data), `embed` (the embed_passages call), `upsert` (the client.upsert call), `doc_save` (the `update_or_create`).
- **Each `timer(...)` is a try/finally** — the spec pitfall #3: even if a phase raises, the partial duration must still be recorded so the access log shows where the request died.
- **Set context vars from the view, not the pipeline:** the upload view (in `apps/documents/views.py`) calls `set_request_context(tenant_id=..., bot_id=...)` after slug validation, then `set_request_context(doc_id=str(doc_id))` once `doc_id` is known. Pipeline doesn't need to know about middleware.
- **WAIT:** spec says "no changes to upload schema, search schema, gRPC proto, or Chunk message" (#17) and "no changes to chunker, embedder, search algorithm, or any payload field" (#18). It does NOT prohibit a small change to `apps/documents/views.py` to call `set_request_context(...)`. That's wiring, not algorithm/schema. Plan adds these one-line calls.
- **Verification:** `uv run python manage.py check`; pipeline tests still green.
- **Rollback:** revert pipeline.py + views.py.
- **Estimated effort:** 25 min.

### Step 3.9 — `apps/grpc_service/server.py` reflection + shutdown

- **Goal:** env-flagged reflection (default OFF); SIGTERM handler from main thread; ensure `server.stop(grace).wait()` runs before the process exits.
- **Diff (key shape):**
  ```python
  ENABLE_REFLECTION = os.environ.get("GRPC_ENABLE_REFLECTION", "").lower() in ("1", "true", "yes")
  GRACEFUL_SHUTDOWN_S = int(os.environ.get("GRPC_SHUTDOWN_GRACE_SECONDS", "10"))

  def serve():
      ...
      search_pb2_grpc.add_VectorSearchServicer_to_server(VectorSearchService(), server)
      if ENABLE_REFLECTION:
          try:
              from grpc_reflection.v1alpha import reflection
              SERVICE_NAMES = (
                  search_pb2.DESCRIPTOR.services_by_name["VectorSearch"].full_name,
                  reflection.SERVICE_NAME,
              )
              reflection.enable_server_reflection(SERVICE_NAMES, server)
              logger.info("grpc_reflection_enabled")
          except ImportError:
              logger.warning("grpc_reflection_unavailable")
      ...
      def _shutdown(signum, frame):
          logger.info("grpc_shutdown_initiated", extra={"signal": signum, "grace_s": GRACEFUL_SHUTDOWN_S})
          stop_event = server.stop(grace=GRACEFUL_SHUTDOWN_S)
          stop_event.wait()
          logger.info("grpc_shutdown_complete")
          sys.exit(0)
      signal.signal(signal.SIGTERM, _shutdown)
      signal.signal(signal.SIGINT, _shutdown)
      server.wait_for_termination()
  ```
- **Main-thread placement:** `serve()` runs in the main thread (per `if __name__ == "__main__": serve()`). Signal install happens inside it, before `wait_for_termination()`. Threads from gRPC's ThreadPoolExecutor cannot intercept signals — only main does. Spec pitfall #7.
- **Verification:** import smoke; `manage.py check` (gRPC server module has django.setup but doesn't run the server).
- **Rollback:** revert server.py.
- **Estimated effort:** 25 min.

### Step 3.10 — `.env.example`

- **Goal:** document new env vars.
- **Diff:** append:
  ```
  # gRPC server polish
  GRPC_ENABLE_REFLECTION=False
  GRPC_SHUTDOWN_GRACE_SECONDS=10
  ```
- **Estimated effort:** 2 min.

### Step 3.11 — `tests/test_observability.py`

- **Goal:** verify the middleware contract + /metrics format + structlog enrichment.
- **Tests (~8):**
  - `test_x_request_id_generated_when_absent` — POST without header → response has X-Request-ID matching UUID4 regex.
  - `test_x_request_id_echoed_when_present` — POST with `X-Request-ID: abc-123` → response header equals input.
  - `test_x_request_id_truncated_at_100_chars` — 200-char input → response trimmed to 100.
  - `test_metrics_endpoint_returns_prometheus_format` — `GET /metrics` → 200, content-type starts with `text/plain`, body contains `qdrant_rag_http_requests_total`.
  - `test_metrics_endpoint_excluded_from_access_log` — capture logs; `GET /metrics` does NOT emit `request_completed`.
  - `test_healthz_excluded_from_access_log` — `GET /healthz` does NOT emit `request_completed`.
  - `test_request_completed_emitted_per_pipeline_endpoint` — POST upload (mocked embedder) → exactly one `request_completed` line with `tenant_id`, `bot_id`, `doc_id`, `phases` dict.
  - `test_contextvar_isolation_across_requests` — issue two requests with different X-Request-IDs; verify the second response carries the correct id and doesn't leak the first.
- **Test isolation for metrics registry:** spec pitfall #4 — the prometheus_client default REGISTRY is a module-level singleton. To avoid "Duplicated timeseries" on re-import, do all metric definitions ONCE at metrics.py module load; tests should NOT re-import metrics.py with `importlib.reload`. If a test needs to reset counter values, use `metric._value.set(0)` (private API) OR accept that tests assert `>= old_value` rather than absolute counts.
- **Verification:** `uv run pytest tests/test_observability.py -v`.
- **Estimated effort:** 60 min (largest single test file).

### Step 3.12 — `tests/test_search_runtime.py`

- **Goal:** RRF smoke + backward-compat regression.
- **Tests:**
  - `test_rrf_dense_outweighs_sparse` — mock `embed_query` to return `dense=[1.0]*1024, sparse={token: 1.0}, colbert=ones((3,1024))`. Use a real Qdrant collection (require `qdrant_available` fixture) populated with two known points: `point_dense` (dense vector aligned with the mock's dense; sparse lexical mismatch) and `point_sparse` (sparse lexical match; dense vector orthogonal). Run search. Assert both surface; assert `point_dense.score / point_sparse.score` ∈ `[1.5, 4.0]`.
  - `test_backward_compat_old_schema_chunk` — **collection created via `apps.qdrant_core.collection.create_collection_for_bot(tenant, bot)` so vector schema is the real Phase 7.5 shape**. Then direct `qdrant_client.upsert(...)` of a single point with the real vector dict (dense=ones(1024), sparse SparseVector, colbert ones((3,1024))) AND a payload that includes deprecated keys (`category`, `tags`, `section_title`) alongside the slim required keys. Run search via `apps.qdrant_core.search.search()`. Assert response valid; assert `chunk["text"] == "old chunk text"`; assert `"category" in chunk` (legacy fields preserved per spec hard constraint behavior). Use unique tenant/bot prefix (uuid-hex8-suffixed); teardown calls `drop_collection(tenant, bot)` in finally.
- **Skip-graceful:** `qdrant_available` fixture (Phase 3 pattern).
- **Estimated effort:** 45 min.

### Step 3.13 — `tests/test_grpc_shutdown.py`

- **Goal:** verify signal handler is installed; opt-in subprocess test for real SIGTERM.
- **Default tests:**
  - `test_signal_handler_registered_for_sigterm` — patch `signal.signal`; call `serve()` (with mocked `server.start`/`wait_for_termination`); assert `signal.signal` called with `signal.SIGTERM` and a callable.
  - `test_shutdown_handler_calls_server_stop_with_grace` — invoke the registered handler; assert `server.stop(grace=10)` called.
- **Opt-in subprocess test:**
  - `test_real_sigterm_drains_within_grace` — gated on `RUN_SIGTERM_TEST=1`; spawn `python -m apps.grpc_service.server` as subprocess; wait for ready; send `SIGTERM`; assert `proc.wait(timeout=15)` returns exit 0.
- **Estimated effort:** 30 min.

### Step 3.14 — `scripts/load_test.py`

- **Goal:** standalone async load smoke.
- **Content (~120 lines):** `httpx.AsyncClient`; CLI `--url http://localhost:8080`, `--uploads 10`, `--searches 100`, `--duration 30`. Uses a unique `(tenant, bot)` per run (timestamp-suffixed slugs). Outputs the report block per spec.
- **Pass criteria** (spec hard constraint #15): zero failed uploads, zero failed searches, p50<100ms, p95<250ms, p99<500ms, chunk count post-test == sum of `chunks_created` from upload responses. **Numbers are baselines, NOT strict SLOs** (re-measured per environment).
- **Not a pytest test:** discovered by pytest only because the file lives under `scripts/`, which is outside `testpaths = ["tests"]` (pyproject.toml). Plan confirms it doesn't get collected.
- **Estimated effort:** 60 min.

### Step 3.15 — Stack rebuild + smoke

- **Goal:** image bakes prometheus-client; new middleware + metrics are live.
- **Commands:**
  ```
  make down
  make rebuild
  sleep 90
  make ps
  make health
  curl -i http://localhost:8080/healthz | grep -i x-request-id
  curl -sS http://localhost:8080/metrics | grep qdrant_rag_
  make logs | grep request_completed | head
  ```
- **gRPC reflection on/off:** toggle `GRPC_ENABLE_REFLECTION=True` in `.env`; `make rebuild`; `grpcurl -plaintext localhost:50051 list` returns `qdrant_rag.v1.VectorSearch`. Toggle back; verify gone.
- **Graceful shutdown smoke:** `docker exec qdrant_rag_grpc kill -TERM 1` → logs show `grpc_shutdown_initiated` then `grpc_shutdown_complete` within ~10s.
- **Estimated effort:** 20 min.

### Step 3.16 — Phase 1-7.6 regression

- **Commands:** `make run pytest -v`; host `uv run pytest -v`; `uv run ruff check .`; `uv run ruff format --check .`.
- **Expected:** all 149+ existing tests still pass; +new tests from 3.11/3.12/3.13.
- **Estimated effort:** 10 min.

### Step 3.17 — Implementation report

- Out-of-scope for this plan; Prompt 3 generates `build_prompts/phase_8a_code_hardening/implementation_report.md`.

---

## 4. Risk register

### R1 [critical] — `request_id` ContextVar leakage between requests
gunicorn workers handle many requests in sequence. Without `var.reset(token)`, stale request_ids persist into subsequent requests. Spec pitfall #1.

**Mitigation:** middleware captures the token from `var.set(value)` and calls `var.reset(token)` in a `finally` block. Test 8 (`test_contextvar_isolation_across_requests`) catches a regression by issuing two consecutive requests with different ids.

### R2 [critical] — `/metrics` self-feedback loop
If the metrics view increments `qdrant_rag_http_requests_total` for itself, every Prometheus scrape adds a counter tick. Spec pitfall #13.

**Mitigation:** `RequestIDMiddleware` and `AccessLogMiddleware` both early-exit on `request.path.startswith(_ACCESS_LOG_EXCLUDED_PREFIXES)` BEFORE any counter increment. Counters are incremented INSIDE the access-log middleware (at request end), so excluding the path skips the increment entirely. Test 5 verifies `/metrics` doesn't emit `request_completed`.

### R3 [critical] — structlog processor ordering
The `_request_context_processor` must run BEFORE the JSON renderer or it adds nothing to the output. Spec pitfall #10.

**Mitigation:** plan §3.6 explicitly inserts the processor BEFORE `ProcessorFormatter.wrap_for_formatter` (the renderer). Verification: a logged event during a request shows `request_id` in the rendered JSON.

### R4 [critical] — Signal handler thread placement
Python signals only deliver to the main thread. If the SIGTERM handler is registered from a sub-thread (e.g., a gRPC handler thread), it never fires. Spec pitfall #7.

**Mitigation:** `serve()` runs in the main thread (`if __name__ == "__main__": serve()`); signal install happens INSIDE `serve()`, before `wait_for_termination()`. Plan §3.9 keeps both lines in main-thread scope.

### R5 [major] — gRPC reflection import safety
`grpcio-reflection` is in dev deps only. Production image (which `uv sync --no-dev`) lacks it. Importing unconditionally crashes the server. Spec pitfall #6.

**Mitigation:** wrap the import in `try: from grpc_reflection.v1alpha import reflection except ImportError: logger.warning(...)`. Server still starts cleanly with reflection disabled.

### R6 [major] — Pipeline phase timer leaks ContextVar state on exception
If a phase raises, the timer must still record the partial duration. Spec pitfall #3.

**Mitigation:** `timer(phase)` uses `try/finally` to record `(time.monotonic() - started) * 1000` regardless of exception. Plan §3.2 specifies this shape.

### R7 [major] — Prometheus metric registration on re-import
Module-level Counter/Histogram/Gauge singletons; re-import (e.g., via `importlib.reload` in tests) hits "Duplicated timeseries". Spec pitfall #4.

**Mitigation:** define metrics ONCE at module load. Tests do not reload metrics.py; they assert `>= old_value` rather than absolute counts.

### R8 [major] — Metric label cardinality
Including `tenant_id` / `bot_id` as labels explodes Prometheus storage as tenant count grows. Spec hard constraint #2.

**Mitigation:** allowed labels per spec: `endpoint` (URL pattern name from `request.resolver_match.url_name`, NOT `request.path`), `method`, `status_code`, `phase`, `rpc`. No tenant_id/bot_id. Spec pitfall #11.

### R9 [major] — Compose `stop_grace_period` < gRPC grace
`docker compose down` defaults to a 10s grace before SIGKILL. With `GRPC_SHUTDOWN_GRACE_SECONDS=10`, near-boundary cancellation is likely (cold-start search needs ~30s, but warm searches finish quickly).

**Mitigation:** v1 acceptable — Compose stop_grace_period is configurable; document raising it to 30s in Phase 8b's compose tweak. Or lower app grace to 8s. Plan goes with the spec default 10s and documents the trade-off.

### R10 [major] — `request_id` header validation
Untrusted client headers; `X-Request-ID: <huge string>` could blow up logs / metric label.

**Mitigation:** truncate to 100 chars in middleware (`str[:100]`); strip leading/trailing whitespace; if empty after strip, generate UUIDv4. Plan §3.3.

### R11 [minor] — gunicorn worker isolation
Each gunicorn worker has its own Prometheus REGISTRY (separate process, no shared memory). `/metrics` from a single worker shows only that worker's metrics; the load balancer routes scrape requests round-robin → metrics jitter.

**Mitigation:** v1 accepts per-worker view (Compose runs `--workers 2`). Phase 8b's nginx + multiprocess prometheus pattern is post-v1. Plan documents.

### R12 [minor] — gRPC server is a separate container
Phase 8a's middleware lives in Django (web container). gRPC has its own log path via the handler's `logger.info(...)` calls. Access-log middleware does NOT cover gRPC. Plan documents that gRPC's per-RPC logging uses the same structlog config (via `apps.core.logging.configure_logging` invoked at gRPC server startup).

**Mitigation:** verify `configure_logging()` is called at gRPC server bootstrap (via Django settings import). Confirmed: `django.setup()` triggers `config/settings.py` which calls `configure_logging(...)`. So gRPC logs flow through the same processor pipeline.

### R13 [minor] — Embedder gauge cross-worker visibility
`qdrant_rag_embedder_loaded` is set in `_get_model()` post-load. Each gunicorn worker has its own gauge; one worker may report 1, another 0. Same per-worker limitation as R11.

**Mitigation:** v1 accepts. Document.

### R14 [minor] — `/healthz`, `/admin/`, `/static/` exclusion from access log
Spec exclude list: `/metrics`. The lens-2 review adds `/healthz` (every probe → log volume) and `/static/`, `/admin/` (Django internals). Plan §3.3 uses a tuple `_ACCESS_LOG_EXCLUDED_PREFIXES = ("/metrics", "/healthz", "/static/", "/admin/")` — broader than spec strictly requires but matches lens-2 guidance.

**Mitigation:** plan goes with the broader exclusion; documented.

### R15 [minor] — Existing tests asserting log line shape
Phases 1-7.6 tests don't assert on the new `request_completed` line (it doesn't exist yet). They assert on event names like `upload_succeeded`. The new structlog processor ADDS keys (`request_id`, etc.) without removing any → existing assertions unaffected.

**Mitigation:** verified by grep (`grep -rn "request_completed\|request_id\|tenant_id" tests/` — only new tests reference these strings).

### R16 [minor] — Backward-compat test pollutes the search collection
The old-schema-payload point is upserted into a Qdrant collection. If the test doesn't clean up, subsequent search tests see the deprecated chunk.

**Mitigation:** plan §3.12 uses unique `(tenant_id, bot_id)` slugs (timestamp-suffixed); teardown calls `drop_collection(...)` in a `finally` block. Phase 3-pattern.

### R17 [minor] — Load test assumes Compose web container is reachable
`scripts/load_test.py` defaults to `http://localhost:8080`. If the user runs it without the stack up, fails fast with httpx ConnectionError. Plan adds a quick health check at startup.

---

## 5. Verification checkpoints

| # | Checkpoint | Command | Expected |
|---|---|---|---|
| 5.1 | uv lock regenerates with new deps | `grep -E "prometheus-client\|grpcio-reflection" uv.lock` | both hits |
| 5.2 | timing.py imports + works | `uv run python -c "from apps.core.timing import timer; ..."` | exit 0 |
| 5.3 | middleware.py imports | `uv run python -c "from apps.core.middleware import RequestIDMiddleware, AccessLogMiddleware, set_request_context; print('ok')"` | "ok" |
| 5.4 | logging.py extension | `uv run python -c "from apps.core.logging import _request_context_processor; print('ok')"` | "ok" |
| 5.5 | metrics.py imports | `uv run python -c "from apps.core.metrics import metrics_view; print('ok')"` | "ok" |
| 5.6 | manage.py check after settings wiring | `uv run python manage.py check` | exit 0 |
| 5.7 | /metrics URL reverses | `reverse('metrics')` → `/metrics` | match |
| 5.8 | pipeline.py imports timer | `grep -n "from apps.core.timing import timer" apps/ingestion/pipeline.py` | one hit |
| 5.9 | gRPC server reflection conditional | `uv run python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); import django; django.setup(); from apps.grpc_service.server import serve; print('ok')"` | "ok" |
| 5.10 | Stack rebuild green | `make rebuild && sleep 90 && make ps && make health` | 6 healthy |
| 5.11 | X-Request-ID echo | `curl -i localhost:8080/healthz` | response includes X-Request-ID? Actually NO — /healthz is excluded. Use a non-excluded path: `curl -i -X POST localhost:8080/v1/tenants/.../search -d '{}'` → header present |
| 5.12 | /metrics body | `curl -sS localhost:8080/metrics` | text/plain with `qdrant_rag_*` metric names |
| 5.13 | request_completed log | `make logs web --tail 100 \| grep request_completed` | at least one entry per recent non-excluded request |
| 5.14 | gRPC reflection toggle | with `GRPC_ENABLE_REFLECTION=True`, `grpcurl -plaintext localhost:50051 list` | `qdrant_rag.v1.VectorSearch` |
| 5.15 | gRPC shutdown smoke | `docker exec qdrant_rag_grpc kill -TERM 1` then logs grep | `grpc_shutdown_initiated` + `grpc_shutdown_complete` within 10s |
| 5.16 | Focused pytest | `make run pytest tests/test_observability.py tests/test_search_runtime.py tests/test_grpc_shutdown.py -v` | all green |
| 5.17 | Full regression | `make run pytest -v` | 149+ prior tests still green; +N new tests |
| 5.18 | Lint+format | `uv run ruff check . && uv run ruff format --check .` | clean |
| 5.19 | Load test | `python scripts/load_test.py` | PASS with baseline numbers |
| 5.20 | Out-of-scope mtime audit | `find ... -newer phase_7_6_implementation_report.md` | only the 13 expected files |

---

## 6. Spec ambiguities & open questions

### A1 — Where do `tenant_id` / `bot_id` / `doc_id` ContextVars get set?
Spec hard constraint #16 says "set by the upload/search views." Plan commits: each view calls `set_request_context(tenant_id=..., bot_id=...)` immediately after slug validation; the upload view additionally calls `set_request_context(doc_id=str(doc_id))` once `doc_id` is resolved (via `body.get("doc_id") or uuid.uuid4()`).

### A2 — Where does `qdrant_rag_embedder_loaded` gauge get set?
Plan commits: in `apps/ingestion/embedder.py::_get_model()`, after the model is constructed. One line: `from apps.core.metrics import set_embedder_loaded; set_embedder_loaded(True)`. Counter-balance: at module import time, set to 0. Note R13: per-worker visibility limitation.

### A3 — Backward-compat test fixture lifetime / Qdrant cleanup
Plan §3.12 uses a per-test `(tenant_id, bot_id)` pair (uuid-hex8-suffixed) and a `finally` that calls `drop_collection(...)`. Same Phase 3-pattern.

### A4 — Load test target URL — env-configurable?
Plan §3.14: CLI flag `--url http://localhost:8080`. No env var override; CLI is sufficient. Document in `--help`.

### A5 — gRPC subprocess SIGTERM test cross-platform skip strategy
Plan §3.13: gated on `os.environ.get("RUN_SIGTERM_TEST") == "1"`. Skip otherwise (pytest.skip with message "set RUN_SIGTERM_TEST=1 to run real-SIGTERM subprocess test"). Default test still verifies handler registration via mocks.

### A6 — Per-request `phases` dict for non-pipeline endpoints — empty vs absent
Spec line 140: "phases is empty for non-pipeline endpoints." Plan §3.3: AccessLogMiddleware always passes `phases=phases` (the dict from `reset_phase_durations()`). For non-pipeline endpoints, no `timer()` call fires, so the dict stays `{}`. Renders as `"phases": {}` in the JSON. Acceptable.

### A7 — gRPC handler logging integration with structlog enrichment
gRPC has its own per-RPC logger inside the handler. Each handler call runs in a ThreadPoolExecutor thread; ContextVars set by the middleware (which runs in the Django web container) DO NOT propagate to gRPC threads (different process). gRPC logs are enriched via the structlog processor running in the gRPC process; the `_request_context_processor` reads ContextVars from the gRPC process's middleware (which... doesn't exist for gRPC). Plan commits: gRPC logs DON'T have `tenant_id`/`bot_id` from ContextVars; they pass these via `extra={...}` directly in the handler's `logger.info(...)` calls (existing pattern from Phase 7).

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope (deferred to Phase 8b)

- `RUNBOOK.md`
- `scripts/snapshot_qdrant.sh`, `scripts/backup_postgres.sh`, `scripts/bootstrap.sh`
- `deploy/qdrant-rag.service`, `deploy/nginx/qdrant_rag.conf.example`
- `.github/workflows/ci.yml`
- New Makefile targets for snapshot/backup/load-test
- Any change to Compose / Dockerfile

### Out of scope (post-v1, never)

- Auth / TLS termination / Celery activation / Redis cache / audit log / quantization / per-tenant config / multi-host

### Phase 8a explicit modifies (5) + new (8) + dep (1)

- **Modified:** `pyproject.toml` (deps), `apps/core/logging.py`, `config/settings.py`, `config/urls.py`, `apps/grpc_service/server.py`, `apps/ingestion/pipeline.py`, `apps/documents/views.py` (one-line `set_request_context` calls — wiring, not algorithm), `.env.example`.
- **New:** `apps/core/{timing,middleware,metrics}.py`, `tests/test_{observability,search_runtime,grpc_shutdown}.py`, `scripts/load_test.py`, `build_prompts/phase_8a_code_hardening/implementation_report.md`.

(Spec lists 5+8+1 = 14; plan adds `apps/documents/views.py` for the ContextVar wiring, making it 14 modified+new + 1 dep change. Documented as deviation 1 in the implementation report.)

---

## 8. Acceptance-criteria mapping

| # | Criterion | Step | Verify | Expected |
|---|---|---|---|---|
| 1 | `GET /metrics` returns 200 Prometheus format | 3.5, 3.7 | step 5.12 | text/plain with `qdrant_rag_*` |
| 2 | All 8 metrics present with at least 1 datapoint | 3.5 | step 5.12 + a few requests | all 8 names appear |
| 3 | Every HTTP response has X-Request-ID | 3.3, 3.6 | step 5.11 | header present |
| 4 | One request_completed per HTTP request with full key set | 3.3, 3.6, 3.8 | step 5.13 | one log line per request; pipeline endpoints have tenant/bot/doc_id + phases |
| 5 | /metrics excluded from request_completed | 3.3 | step 5.13 (grep does NOT see /metrics requests) | no entries |
| 6 | Every log within request scope auto-enriched with request_id | 3.4, 3.6 | step 5.13 + grep | all lines in window have request_id |
| 7 | gRPC reflection toggle | 3.9, 3.10 | step 5.14 | with True → list works; False → list fails |
| 8 | SIGTERM drains within grace | 3.9 | step 5.15 | shutdown logs within 10s |
| 9 | Three new test files green | 3.11-3.13 | step 5.16 | all green |
| 10 | RRF score ratio ∈ [1.5, 4.0] | 3.12 | step 5.16 | test_rrf_dense_outweighs_sparse PASS |
| 11 | Backward-compat test passes | 3.12 | step 5.16 | test_backward_compat_old_schema_chunk PASS |
| 12 | Load test PASSes with baseline numbers | 3.14 | step 5.19 | PASS line printed |
| 13 | `make rebuild && make ps` 6 healthy | 3.15 | step 5.10 | 6 healthy |
| 14 | Phase 1-7.6 regression | 3.16 | step 5.17 | all prior green |

---

## 9. Tooling commands cheat-sheet

```bash
# Deps
uv lock
grep -E "prometheus-client|grpcio-reflection" uv.lock

# Sanity
uv run python manage.py check
uv run ruff check . && uv run ruff format --check .

# Tests
make run pytest tests/test_observability.py tests/test_search_runtime.py tests/test_grpc_shutdown.py -v
make run pytest -v
uv run pytest tests/test_observability.py -v   # host (skip-graceful for embedder)

# Stack
make down && make rebuild && sleep 90 && make ps && make health

# Smoke — observability
curl -i -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"obs smoke"}]}' | head -20

curl -sS http://localhost:8080/metrics | head -40
make logs web --tail 100 | grep request_completed | head

# gRPC reflection (toggle in .env)
echo "GRPC_ENABLE_REFLECTION=True" >> .env
make rebuild && sleep 30
grpcurl -plaintext localhost:50051 list

# Graceful shutdown
docker exec qdrant_rag_grpc kill -TERM 1
make logs grpc --tail 30 | grep -E "shutdown_(initiated|complete)"

# Load test
python scripts/load_test.py --uploads 10 --searches 100

# Out-of-scope mtime audit (no git)
find apps/core/{apps,__init__,urls,views}.py \
     apps/qdrant_core apps/grpc_service/{__init__,apps,handler}.py \
     apps/grpc_service/generated apps/tenants apps/documents/{models,admin,serializers,urls,exceptions,migrations}.py \
     apps/ingestion/{embedder,chunker,payload,locks}.py \
     proto Dockerfile docker-compose.yml Makefile \
     scripts/{compile_proto,verify_setup}.py \
     tests/test_{healthz,models,naming,qdrant_client,qdrant_collection,chunker,payload,embedder,upload,locks,delete,pipeline,search_grpc,search_query,search_http}.py \
     tests/conftest.py tests/test_settings.py tests/fixtures \
     -newer build_prompts/phase_7_6_raw_payload/implementation_report.md \
     2>/dev/null
# expect empty (or only the Phase 8a scope files)
```

---

## 10. Estimated effort

| Step | Estimate |
|---|---|
| 3.1 pyproject + uv lock | 5 min |
| 3.2 timing.py | 10 min |
| 3.3 middleware.py | 30 min |
| 3.4 logging.py extension | 15 min |
| 3.5 metrics.py | 30 min |
| 3.6 settings wiring | 10 min |
| 3.7 urls.py /metrics route | 5 min |
| 3.8 pipeline timer wrap + views set_request_context | 25 min |
| 3.9 server.py reflection + shutdown | 25 min |
| 3.10 .env.example | 2 min |
| 3.11 test_observability.py (8 tests) | 60 min |
| 3.12 test_search_runtime.py (2 tests) | 45 min |
| 3.13 test_grpc_shutdown.py (3 tests) | 30 min |
| 3.14 scripts/load_test.py | 60 min |
| 3.15 stack rebuild + smokes | 20 min |
| 3.16 regression | 10 min |
| 3.17 implementation_report.md (Prompt 3) | 30 min |
| **Total** | **~7 hours** (largest phase yet by far) |

---

## End of plan
