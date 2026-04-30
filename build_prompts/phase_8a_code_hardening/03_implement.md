# Phase 8a — Step 3 of 3: Implement & Verify

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Now you write code. Follow the revised plan.**

---

## Required reading (in this order)

1. `build_prompts/phase_8a_code_hardening/spec.md` — non-negotiable.
2. `build_prompts/phase_8a_code_hardening/plan.md` — revised plan after Step 2 review.
3. `build_prompts/phase_8a_code_hardening/plan_review.md` — review findings (especially [critical] / [major]).
4. `build_prompts/phase_7_search_grpc/implementation_report.md` — Phase 7 baseline.
5. `build_prompts/phase_7_5_api_cleanup/implementation_report.md`, `phase_7_6_raw_payload/implementation_report.md` — recent baselines.
6. `apps/core/logging.py`, `apps/grpc_service/server.py`, `apps/ingestion/pipeline.py`, `apps/qdrant_core/search.py`, `config/settings.py`, `config/urls.py` — current state.
7. `pyproject.toml` — current deps.

If any of these don't exist, abort.

---

## Your task

Implement Phase 8a per the revised plan. **8 new files + 5 modified + 1 dependency change.**

### New
1. `apps/core/timing.py`
2. `apps/core/middleware.py`
3. `apps/core/metrics.py`
4. `tests/test_observability.py`
5. `tests/test_search_runtime.py`
6. `tests/test_grpc_shutdown.py`
7. `scripts/load_test.py`

### Modified
8. `apps/core/logging.py` — add the request-context-enrichment structlog processor
9. `config/settings.py` — register middleware + structlog processor
10. `config/urls.py` — add `/metrics` route
11. `apps/ingestion/pipeline.py` — wrap pipeline phases with `timer()`
12. `apps/grpc_service/server.py` — reflection toggle + signal handler
13. `pyproject.toml` — add `prometheus-client>=0.20` (main), `grpcio-reflection>=1.60` (dev)
14. `.env.example` — document `GRPC_ENABLE_REFLECTION` (default False), `GRPC_SHUTDOWN_GRACE_SECONDS` (default 10)

---

## Hard rules

1. **Implement only what the plan says.** No drive-by refactors.
2. **Do NOT modify any file outside the modification list.**
3. **Do NOT use `tenant_id` or `bot_id` as Prometheus labels** (cardinality bound).
4. **`/metrics`, `/healthz`, `/static/`, `/admin/` excluded from access log middleware.** Path-prefix early-exit.
5. **gRPC reflection import wrapped in try/except ImportError** so prod (without grpcio-reflection installed) doesn't crash on startup.
6. **`request_id` middleware must use `var.reset(token)` in finally** to prevent ContextVar leakage across requests.
7. **Pipeline `timer(phase)` wraps via try/finally** so failed phases still record duration.
8. **Signal handler registered from main thread only** in gRPC server bootstrap.
9. **`make rebuild`** (not `make wipe`) — bge_cache + Postgres data preserved.
10. **No schema change, no migration.**
11. **Default to `GRPC_ENABLE_REFLECTION=False` in `.env.example`.**
12. **Default `GRPC_SHUTDOWN_GRACE_SECONDS=10`** unless plan revised after lens 2 finding about Compose stop_grace_period.
13. **Tests stay green.** Phase 1-7.6 regression: full suite passes.

---

## Step-by-step

### Step 1 — `pyproject.toml` + `uv lock`

Add `prometheus-client>=0.20` to main `dependencies`. Add `grpcio-reflection>=1.60` to `[dependency-groups]` `dev` section. Run `uv lock` on host.

Verify: `grep prometheus-client uv.lock` succeeds; `grep grpcio-reflection uv.lock` succeeds.

### Step 2 — `apps/core/timing.py`

```python
"""Per-request phase timer backed by ContextVar.

Pipeline code wraps phases with `timer("embed"):`. The middleware reads the
accumulated dict at request end and writes it to the access-log line.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from typing import Iterator

_phase_durations_var: contextvars.ContextVar[dict[str, float]] = contextvars.ContextVar(
    "phase_durations", default=None
)


def reset_phase_durations() -> contextvars.Token:
    return _phase_durations_var.set({})


def get_phase_durations() -> dict[str, float]:
    return _phase_durations_var.get() or {}


def restore_phase_durations(token: contextvars.Token) -> None:
    _phase_durations_var.reset(token)


@contextlib.contextmanager
def timer(phase: str) -> Iterator[None]:
    """Record duration of `phase` in the request-scoped ContextVar dict.

    Records duration even on exception (try/finally). No-op if no
    request scope is active (ContextVar default is None).
    """
    durations = _phase_durations_var.get()
    if durations is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        durations[phase] = (time.monotonic() - start) * 1000.0  # ms
```

### Step 3 — `apps/core/middleware.py`

Two middlewares:
- `RequestIDMiddleware` — set `_request_id_var`; echo header; reset on exit.
- `AccessLogMiddleware` — wrap with phase timing; emit `request_completed` log line on response; skip excluded paths.

Excluded path prefixes: `("/metrics", "/healthz", "/static/", "/admin/")`.

ContextVars (also exported for the structlog processor):
```python
import contextvars

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)
_bot_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("bot_id", default=None)
_doc_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("doc_id", default=None)
```

`RequestIDMiddleware`:
- Read `X-Request-ID` header (truncate to 100 chars; default to `uuid.uuid4().hex`).
- `var.set(request_id)` → token; pass through; `var.reset(token)` in finally.
- Set `response["X-Request-ID"] = request_id` before returning.

`AccessLogMiddleware`:
- Skip excluded paths (early return; no log, no timing).
- Reset phase durations dict (`reset_phase_durations()`); capture token.
- Time the request (monotonic).
- On response, log `request_completed` with: `request_id`, `method`, `path`, `status_code`, `duration_ms`, `phases` (from `get_phase_durations()`), and (if set) `tenant_id`/`bot_id`/`doc_id` from their ContextVars. Omit keys whose ContextVars are None.
- Restore the phase durations token in finally.

The view code (or pipeline) must call `_tenant_id_var.set(...)` / `_bot_id_var.set(...)` / `_doc_id_var.set(...)` to populate those fields. Add helper `set_request_context(tenant_id=..., bot_id=..., doc_id=...)` for view convenience.

### Step 4 — `apps/core/logging.py` extension

Add a structlog processor that reads from the four ContextVars and merges into every log event. Place BEFORE the JSON renderer.

```python
def _request_context_processor(logger, method_name, event_dict):
    from apps.core.middleware import _request_id_var, _tenant_id_var, _bot_id_var, _doc_id_var
    rid = _request_id_var.get()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    tid = _tenant_id_var.get()
    if tid is not None:
        event_dict.setdefault("tenant_id", tid)
    bid = _bot_id_var.get()
    if bid is not None:
        event_dict.setdefault("bot_id", bid)
    did = _doc_id_var.get()
    if did is not None:
        event_dict.setdefault("doc_id", did)
    return event_dict
```

`setdefault` so explicit `extra={"request_id": ...}` calls still win.

Insert the processor in the existing structlog config processor list, BEFORE the JSON renderer.

### Step 5 — `apps/core/metrics.py`

```python
"""Prometheus metrics. v1 single-process; multi-worker is per-worker view."""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

http_requests_total = Counter(
    "qdrant_rag_http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "qdrant_rag_http_request_duration_seconds",
    "End-to-end HTTP latency in seconds",
    ["method", "endpoint"],
)

pipeline_phase_duration_seconds = Histogram(
    "qdrant_rag_pipeline_phase_duration_seconds",
    "Per-phase upload latency in seconds",
    ["phase"],
)

grpc_requests_total = Counter(
    "qdrant_rag_grpc_requests_total",
    "Total gRPC requests received",
    ["rpc", "status_code"],
)

grpc_request_duration_seconds = Histogram(
    "qdrant_rag_grpc_request_duration_seconds",
    "End-to-end gRPC latency in seconds",
    ["rpc"],
)

search_results_count = Histogram(
    "qdrant_rag_search_results_count",
    "Distribution of total_candidates per search",
    buckets=(0, 1, 2, 5, 10, 20, 50, 100),
)

search_threshold_used = Gauge(
    "qdrant_rag_search_threshold_used",
    "Last reported threshold_used (0.0 means disabled)",
)

embedder_loaded = Gauge(
    "qdrant_rag_embedder_loaded",
    "1 if BGE-M3 has loaded in this worker; 0 otherwise",
)


def metrics_view(request: HttpRequest) -> HttpResponse:
    """Prometheus exposition. Unauthenticated; nginx scopes to internal IPs in 8b."""
    return HttpResponse(generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)
```

### Step 6 — `config/settings.py` and `config/urls.py`

`settings.py`:
- Add to MIDDLEWARE: `"apps.core.middleware.RequestIDMiddleware"` (FIRST among Django middleware), `"apps.core.middleware.AccessLogMiddleware"` (LAST among Django middleware, AFTER security/whitenoise).
- Update structlog processors to include `_request_context_processor` BEFORE the JSON renderer.

`urls.py`:
- Add `path("metrics", apps.core.metrics.metrics_view, name="metrics")` at the same level as `healthz`.

### Step 7 — `apps/ingestion/pipeline.py` instrumentation

Wrap each phase in `timer(...)`:
```python
with timer("get_or_create"): ...
with timer("dedup_check"): ...
with timer("chunk"): ...
with timer("embed"): ...
with timer("upsert"): ...
with timer("doc_save"): ...
```

Also call `set_request_context(tenant_id=..., bot_id=..., doc_id=...)` early in the pipeline so the access log carries them and the structlog processor enriches subsequent log calls.

Same for upload/search/delete views — they already know the tenant/bot/doc context.

### Step 8 — `apps/grpc_service/server.py`

Add to bootstrap:
1. Read `GRPC_ENABLE_REFLECTION` (default False), `GRPC_SHUTDOWN_GRACE_SECONDS` (default 10) from env.
2. After `add_VectorSearchServicer_to_server(...)`:
   ```python
   if os.environ.get("GRPC_ENABLE_REFLECTION", "False").lower() == "true":
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
   ```
3. Register signal handler IN MAIN THREAD before `server.start()`:
   ```python
   import signal

   def _shutdown(signum, frame):
       logger.info("grpc_shutdown_initiated", extra={"signal": signum})
       server.stop(grace=grace_seconds).wait()
       logger.info("grpc_shutdown_complete")
       sys.exit(0)

   signal.signal(signal.SIGTERM, _shutdown)
   ```

### Step 9 — `.env.example`

Add:
```
# gRPC reflection — enable for staging only
GRPC_ENABLE_REFLECTION=False

# Graceful shutdown drain window in seconds
GRPC_SHUTDOWN_GRACE_SECONDS=10
```

### Step 10 — Tests

#### `tests/test_observability.py`
- `test_request_id_generated_when_header_absent` — assert response includes `X-Request-ID`.
- `test_request_id_echoed_when_header_present` — assert echoed value matches.
- `test_request_completed_log_emitted` — assert log captures `request_completed` event with required keys.
- `test_metrics_endpoint_returns_prometheus_format` — assert content-type, status 200, body contains `qdrant_rag_http_requests_total`.
- `test_metrics_endpoint_excluded_from_access_log` — assert no `request_completed` log line for `/metrics` requests.
- `test_healthz_excluded_from_access_log` — assert no `request_completed` log line for `/healthz` requests.
- `test_request_id_isolation_across_requests` — two sequential requests get different IDs.
- `test_structlog_enrichment_includes_request_id` — log inside a request is enriched with `request_id`.

#### `tests/test_search_runtime.py`
- `test_rrf_smoke_dense_outweighs_sparse` — fixture with `point_dense` and `point_sparse`; assert ratio in `[1.5, 4.0]`.
- `test_backward_compat_old_schema_payload` — directly upsert old-schema point; search returns valid response with legacy fields preserved in payload.

#### `tests/test_grpc_shutdown.py`
- Default mode: `test_signal_handler_registered` — patch `signal.signal`, import server module, assert SIGTERM handler set; `test_shutdown_calls_server_stop_with_grace` — invoke handler, assert `server.stop(grace=...)` called with configured value.
- Opt-in: `test_real_sigterm_drains_inflight_request` — skipped unless `RUN_SIGTERM_TEST=1`. Spawns subprocess, sends search, sends SIGTERM, asserts clean exit + completed-or-cancelled-cleanly request.

### Step 11 — `scripts/load_test.py`

Standalone async script. Uses `httpx.AsyncClient` (already in dev deps via httpx). CLI: `--uploads N`, `--searches M`, `--duration S`, `--url URL`. Default `http://localhost:8080`. Output as in spec.

### Step 12 — Rebuild and verify

```bash
make rebuild
make ps                    # all 6 healthy
make health                # JSON ok
curl -i http://localhost:8080/healthz | grep X-Request-ID         # header present
curl -sS http://localhost:8080/metrics | grep qdrant_rag_         # metrics enumerated
make run pytest tests/test_observability.py tests/test_search_runtime.py tests/test_grpc_shutdown.py -v
make run pytest -v          # full regression
```

### Step 13 — gRPC reflection smoke

```bash
# Enable in .env, restart
echo "GRPC_ENABLE_REFLECTION=True" >> .env
make rebuild

grpcurl -plaintext localhost:50051 list
# Expect: qdrant_rag.v1.VectorSearch + grpc.reflection.v1alpha.ServerReflection

# Disable again, restart
sed -i 's/GRPC_ENABLE_REFLECTION=True/GRPC_ENABLE_REFLECTION=False/' .env
make rebuild

grpcurl -plaintext localhost:50051 list
# Expect: error / refused (reflection not available)
```

### Step 14 — Graceful shutdown smoke

```bash
docker exec qdrant_rag_grpc kill -TERM 1
docker compose logs grpc | tail -20
# Expect: "grpc_shutdown_initiated" then "grpc_shutdown_complete" within ~10s
docker compose start grpc
```

### Step 15 — Load smoke

```bash
python scripts/load_test.py --uploads 10 --searches 100
# Expect: PASS, baseline numbers reported
```

---

## Implementation report

Write `build_prompts/phase_8a_code_hardening/implementation_report.md`:
1. Status: PASS / PARTIAL / FAIL.
2. Files modified/created with line counts.
3. Tests count + names + pass/fail.
4. All 14 acceptance criteria with PASS/FAIL/SKIP.
5. Manual smoke output for: `/metrics`, `X-Request-ID`, `request_completed` log, gRPC reflection, graceful shutdown, load test.
6. Phase 1-7.6 regression status.
7. `make ps` snapshot.
8. Notable deviations from plan + justification.
9. Recommended next step (likely: Phase 8b).

---

## What "done" looks like

Output to chat:
1. All 8 new + 5 modified + 1 dependency change applied.
2. PASS/FAIL on each acceptance criterion.
3. Manual smoke confirmations.
4. Test count delta.
5. Recommended next step.

Then **stop**.
