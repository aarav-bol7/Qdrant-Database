# Phase 8a — Code-side Hardening

> **Audience:** A coding agent building on top of verified-green Phases 1–7.6 at `/home/bol7/Documents/BOL7/Qdrant`. Phase 8a is the first half of the original Phase 8 (Hardening & Ship). It covers in-process observability, search-quality runtime verification, gRPC production polish, and load smoke tests. Phase 8b (a separate later phase) covers operational artifacts (deployment scripts, nginx, RUNBOOK, CI, snapshot/backup).

---

## Mission

Make the running app **operable**: observable enough to debug an incident, instrumented enough to prove the search algorithm hasn't drifted, gracefully terminating under SIGTERM, and verified under realistic concurrency.

Four scope buckets, all entirely inside the Python codebase:

1. **Observability** — Prometheus `/metrics` endpoint, request_id middleware (ContextVar-based), structlog processor enriching every log line with `request_id`/`tenant_id`/`bot_id`/`doc_id`, single per-request access log with timing breakdown.
2. **Search-quality runtime verification** — deterministic smoke test of the RRF emulation; backward-compat regression test that searches against a manually-constructed old-schema Qdrant point.
3. **gRPC production polish** — reflection toggle (env-flagged, default OFF), graceful shutdown handler that drains in-flight requests on SIGTERM within a configurable window, registered shutdown signal handler.
4. **Load smoke tests** — `scripts/load_test.py` issuing 10 concurrent uploads + 100 concurrent searches, measuring p50/p95/p99 latency, asserting no chunk loss.

After Phase 8a: every HTTP request and every gRPC call writes one structured access-log line with `request_id`, `tenant_id`, `bot_id`, `doc_id`, and timing; Prometheus scrapes per-endpoint counters and latency histograms at `/metrics`; gRPC server stops cleanly on SIGTERM; a load script proves the system handles 100 concurrent searches within a documented latency budget.

---

## Why now

- The system has been functionally complete since Phase 7. Tuning has stopped (post-Phase-7.5 polish wrapped). The remaining work is *operational* — make it possible to diagnose what's happening in production.
- Phase 8b (operational artifacts: bootstrap scripts, nginx, RUNBOOK, CI workflow, snapshot scripts) is a separate phase to keep each phase under ~12 files.

---

## Read first

- `README.md` — current API surface and architecture.
- `build_prompts/phase_7_search_grpc/spec.md` — gRPC server contract; this phase adds shutdown handler and reflection toggle.
- `build_prompts/phase_7_search_grpc/implementation_report.md` — Phase 7 outcomes (RRF emulation via duplicated dense Prefetch).
- `build_prompts/phase_7_5_api_cleanup/spec.md` — backward-compat note: old gRPC clients with stale proto deserialize new responses fine via proto3 unknown-field handling. The backward-compat regression test in 8a exercises this from the Qdrant point side.
- `build_prompts/phase_5b_upload_idempotency/spec.md` — upload pipeline phases (validate / lock / hash check / chunk / embed / upsert) that the access log will time.
- `apps/core/logging.py` — current structlog config; this phase extends it.
- `apps/grpc_service/server.py` — current gRPC server bootstrap; this phase adds shutdown + reflection.
- `apps/ingestion/pipeline.py` — pipeline timing points the access log will tap.
- `apps/qdrant_core/search.py` — search function the runtime verification tests target.
- `config/settings.py`, `config/urls.py` — Django wiring points.
- `pyproject.toml` — dependency manifest; this phase adds `prometheus-client`.

---

## Hard constraints

1. **Single new dependency.** `prometheus-client>=0.20`. No `django-prometheus` (over-instruments and adds magic). Hand-roll metric registration.

2. **Metric label cardinality is bounded.** Do **not** include `tenant_id` or `bot_id` as Prometheus labels — that explodes storage as tenant count grows. Per-tenant breakdown lives in logs (which have these labels via the structlog processor). Allowed labels: `endpoint`, `method`, `status_code`, `phase` (for pipeline-stage histograms).

3. **`request_id` is ContextVar-based.** A `contextvars.ContextVar` carries the id through the request lifecycle. The middleware sets it at request entry; the structlog processor reads it at every log call. No manual `extra={"request_id": ...}` at call sites.

4. **`request_id` injected from header if present.** Header name: `X-Request-ID`. If absent, server generates a UUIDv4. Returned in the response header so callers can correlate.

5. **One access log per request, not per phase.** The middleware accumulates phase timings into a single dict and emits one log line at request end. Phase timings come from a `ContextVar`-backed timer helper that pipeline code calls (`with timer("embed"):`).

6. **Pipeline phase timing instrumentation is non-invasive.** The pipeline gains a `with timer("embed"):` wrapper around each phase but no new arguments, no new return values. The timer helper writes to a ContextVar dict consumed by the access-log middleware at request end.

7. **`/metrics` endpoint is unauthenticated.** v1 acceptable. Phase 8b's nginx config will scope it to internal IPs at the edge. Spec must include a comment on the route definition documenting this expectation.

8. **`/metrics` excluded from request_id middleware and access log.** Metric scraping happens hundreds of times per minute; logging each scrape would dominate logs. Skip both via path check.

9. **gRPC reflection is env-flagged.** Env var `GRPC_ENABLE_REFLECTION`, default `False`. When `True`, server registers `grpc_reflection.v1alpha.reflection`. The proto stubs and `grpcio-reflection` package handle the rest. Add `grpcio-reflection>=1.60` to the dev dependency group (not main; production stays slim).

10. **Graceful shutdown handler.** `apps/grpc_service/server.py` registers a SIGTERM handler that calls `server.stop(grace=GRPC_SHUTDOWN_GRACE_SECONDS)` (env var, default 10 seconds). Server drains in-flight `Search` calls during the grace window. After the grace window expires, remaining calls are forcefully cancelled.

11. **Shutdown test reliability.** Default test verifies the signal handler is registered and the stop method drains correctly using a mocked clock. The deterministic real-SIGTERM-against-subprocess test is opt-in via `RUN_SIGTERM_TEST=1` env var (skips otherwise). Reason: real subprocess signal tests are flaky on macOS / CI runners.

12. **RRF runtime verification is a deterministic smoke test, not a statistical assertion.** Construct a fixture with two known chunks (one strongly matching dense embedding, one strongly matching sparse). Run search. Assert both surface in top-K with scores in expected ratio (no exact float assert; bounded ratio check). No real BGE-M3 — `embed_query` is mocked to return controlled vectors.

13. **Backward-compat regression test constructs the old-schema chunk via direct `qdrant_client.upsert`.** Not via the upload pipeline (which would strip the old fields). The point's payload includes deprecated fields like `category`, `tags`, `section_title`. Search against it returns a valid response (proto3 silently drops unknown fields on the gRPC side, and the new payload-reading code uses `payload.get(...)` patterns that are safe with extras).

14. **Load smoke test is opt-in.** `scripts/load_test.py` is a standalone async script (not a pytest test). Run via `python scripts/load_test.py [--uploads N] [--searches M]`. Default: 10 uploads, 100 searches, 30s warm. NOT run by `pytest`. Documented as a manual verification step.

15. **Load test pass criteria are documented baselines, not strict SLOs.** v1: p50 search < 100 ms, p95 < 250 ms, p99 < 500 ms (warm); zero failed requests; zero chunk loss verified by post-test count comparison. Baseline numbers re-measured per-environment.

16. **Per-request structlog enrichment is processor-level, not call-site-level.** A new structlog processor reads the `request_id` ContextVar (and a `tenant_id`/`bot_id`/`doc_id` ContextVar set by the upload/search views) and merges them into every log event. Existing log calls don't change.

17. **No changes to upload schema, search schema, gRPC proto, or Chunk message.** This phase is purely instrumentation + verification.

18. **No changes to chunker, embedder, search algorithm, or any payload field.**

19. **Tests stay green.** Phase 1-7.6 regression: full suite passes after the new code lands.

20. **`make rebuild`** (not `make wipe`) preserves bge_cache and Postgres data. The new code only requires `pip install` of `prometheus-client` (handled by uv sync during build) — no migration, no schema change.

---

## Files modified / created

### New (6)

1. `apps/core/middleware.py` — `RequestIDMiddleware` (ContextVar set + response header) + `AccessLogMiddleware` (single line per request with phase timings)
2. `apps/core/metrics.py` — Prometheus registry + counter/histogram definitions + `/metrics` view
3. `apps/core/timing.py` — `timer(phase: str)` context manager backed by a `ContextVar[dict]` that the access log reads at request end
4. `apps/core/logging.py` — modify (not new) the existing structlog config to add the request_id-enrichment processor (this is "extend an existing file")
5. `tests/test_observability.py` — covers middleware + metrics + structlog enrichment + per-request access log
6. `tests/test_search_runtime.py` — RRF smoke test + backward-compat regression test
7. `tests/test_grpc_shutdown.py` — graceful shutdown handler test (default mode + opt-in subprocess mode)
8. `scripts/load_test.py` — async load smoke script

### Modified (5)

9. `config/settings.py` — add `apps.core.middleware.RequestIDMiddleware` and `AccessLogMiddleware` to `MIDDLEWARE` list (order: RequestID first, AccessLog last among Django middleware)
10. `config/urls.py` — add `/metrics` route → `apps.core.metrics.metrics_view`
11. `apps/grpc_service/server.py` — add SIGTERM signal handler + reflection toggle (env-flagged) + shutdown grace window
12. `apps/ingestion/pipeline.py` — wrap pipeline phases (lock/get_or_create/chunk/embed/upsert) in `timer()` calls
13. `pyproject.toml` — add `prometheus-client>=0.20` to main deps; add `grpcio-reflection>=1.60` to dev deps (not main; reflection is dev-only)
14. `.env.example` — document `GRPC_ENABLE_REFLECTION` (default False), `GRPC_SHUTDOWN_GRACE_SECONDS` (default 10)

---

## Behavior — exact contracts

### `request_id`

```
Header in:  X-Request-ID  (optional; if missing, server generates UUIDv4)
ContextVar: apps.core.middleware._request_id_var (set by middleware)
Header out: X-Request-ID  (always; matches the value used during the request)
```

### Access log line shape (one per request, JSON)

```json
{
  "event": "request_completed",
  "request_id": "...",
  "method": "POST",
  "path": "/v1/tenants/test_t/bots/test_b/documents",
  "status_code": 201,
  "duration_ms": 1234.5,
  "tenant_id": "test_t",
  "bot_id": "test_b",
  "doc_id": "...",
  "phases": {"lock": 5.2, "chunk": 12.4, "embed": 980.1, "upsert": 230.7}
}
```

`phases` is empty for non-pipeline endpoints (e.g. `/healthz`, `/admin/`, `/metrics` — though `/metrics` is excluded entirely).

### Prometheus metrics (initial set)

| Metric | Type | Labels | What it measures |
|---|---|---|---|
| `qdrant_rag_http_requests_total` | Counter | `method`, `endpoint`, `status_code` | Per-endpoint request count |
| `qdrant_rag_http_request_duration_seconds` | Histogram | `method`, `endpoint` | End-to-end HTTP latency |
| `qdrant_rag_pipeline_phase_duration_seconds` | Histogram | `phase` | Per-phase upload latency |
| `qdrant_rag_grpc_requests_total` | Counter | `rpc`, `status_code` | gRPC request count |
| `qdrant_rag_grpc_request_duration_seconds` | Histogram | `rpc` | gRPC latency |
| `qdrant_rag_search_results_count` | Histogram | (none) | Distribution of `total_candidates` |
| `qdrant_rag_search_threshold_used` | Gauge | (none) | Last reported `threshold_used` |
| `qdrant_rag_embedder_loaded` | Gauge | (none) | 1 if BGE-M3 loaded; 0 otherwise |

`endpoint` is the URL pattern name (e.g. `upload-document`, `search-documents`, `delete-document`), not the literal path — keeps cardinality at fixed-set.

### gRPC server bootstrap

- Server start path:
  1. Build `gRPC server` with current options
  2. Register handlers
  3. If `GRPC_ENABLE_REFLECTION=True`: register reflection
  4. Register SIGTERM handler that calls `server.stop(grace=GRPC_SHUTDOWN_GRACE_SECONDS)` then exits
  5. `server.start()`; `server.wait_for_termination()`
- Server stop path (under SIGTERM):
  1. Stop accepting new requests immediately
  2. Wait up to `grace` seconds for in-flight requests to complete
  3. Force-cancel anything still running
  4. Exit cleanly

### Search runtime verification

#### RRF smoke test
1. Setup: mock `embed_query` to return two distinct vectors. Use a real (in-memory) Qdrant collection populated with two known points: `point_dense` (matches the dense vector strongly) and `point_sparse` (matches the sparse vector strongly).
2. Run search.
3. Assert:
   - Both points returned in `chunks`
   - `point_dense.score > point_sparse.score` (or the inverse — depends on which gets the 3x weighting; spec commits to dense=3x)
   - The ratio `point_dense.score / point_sparse.score` is within `[1.5, 4.0]` (loose bound — exercises ratio is roughly 3:1 but tolerates implementation details)

#### Backward-compat regression test
1. Setup: directly upsert a Qdrant point with the OLD payload schema:
   ```python
   payload = {
     "chunk_id": "...", "doc_id": "...", "tenant_id": "...", "bot_id": "...",
     "text": "old chunk text", "source_type": "pdf", "is_active": True,
     "section_path": [], "page_number": 1,
     # Deprecated fields:
     "category": "Finance", "tags": ["q3"], "section_title": "Revenue",
   }
   ```
2. Run search via `apps.qdrant_core.search.search()`.
3. Assert: response valid; chunk returned with `text == "old chunk text"`; deprecated fields are NOT in the returned chunk dict (they're in Qdrant payload but the search response's payload-mapping code drops unknowns implicitly OR keeps them — spec must commit on which).

→ **Commit:** the search response keeps unknown payload fields. The current code does `payload = dict(p.payload or {})`, which preserves all. The test asserts `chunk["text"] == "old chunk text"` AND `"category" in chunk` (legacy passes through). HTTP search response shape is documented as "may include legacy fields for backward compat with old data."

### gRPC shutdown test

#### Default mode (always runs)
- Construct the gRPC server in-process
- Mock `os.kill` / signal handler registration
- Assert: signal handler registered for SIGTERM; calling the handler invokes `server.stop(grace=...)` with the configured grace value
- Mocked clock: assert grace window respected

#### Opt-in subprocess mode (`RUN_SIGTERM_TEST=1`)
- Spawn the gRPC server in a subprocess
- Send a `Search` request in a background thread
- Send SIGTERM to the subprocess
- Assert: the subprocess exits within `grace + 5` seconds; the in-flight `Search` request either completed or was cancelled with a clear gRPC status

### Load smoke test

```
$ python scripts/load_test.py --uploads 10 --searches 100 --duration 30

[load test] target: http://localhost:8080
[load test] warming up... (single upload + single search)
[load test] starting 10 concurrent uploads
[load test] uploads complete: 10/10 ok in 4.2s
[load test] starting 100 concurrent searches over 30s
[load test] searches complete: 3000/3000 ok
[load test] latency: p50=92ms p95=210ms p99=410ms
[load test] chunk loss check: 100 uploaded chunks → 100 in Qdrant. PASS
[load test] PASS
```

Pass criteria (defaults):
- Zero failed uploads
- Zero failed searches
- p50 < 100ms, p95 < 250ms, p99 < 500ms
- Chunk count post-test == sum of `chunks_created` across upload responses

---

## Acceptance criteria

1. `GET /metrics` returns 200 with Prometheus exposition format (text/plain).
2. The metrics output includes all metrics listed in the table above. Each metric has at least one observed datapoint after a few HTTP requests.
3. Every HTTP response includes an `X-Request-ID` header (echoed if supplied; generated if not).
4. Server logs include exactly one `request_completed` event per HTTP request, JSON-formatted, with `request_id` / `method` / `path` / `status_code` / `duration_ms`. Pipeline endpoints additionally include `tenant_id` / `bot_id` / `doc_id` and a `phases` dict with timings.
5. `/metrics` endpoint requests are NOT logged via `request_completed`.
6. Every log line generated within a request scope is automatically enriched with `request_id` (no manual `extra` argument).
7. With `GRPC_ENABLE_REFLECTION=True`, `grpcurl -plaintext localhost:50051 list` returns `qdrant_rag.v1.VectorSearch`. With `=False` (default), reflection is unavailable.
8. Sending SIGTERM to the gRPC container drains in-flight requests within `GRPC_SHUTDOWN_GRACE_SECONDS` (default 10s) and exits with code 0.
9. `make run pytest tests/test_observability.py tests/test_search_runtime.py tests/test_grpc_shutdown.py -v` all green.
10. RRF smoke test asserts dense:sparse score ratio is in `[1.5, 4.0]`.
11. Backward-compat test passes: search against an old-schema-payload chunk returns a valid response.
12. `python scripts/load_test.py` with default parameters completes within budget on a 4-core dev machine; reports baseline p50/p95/p99 numbers.
13. `make rebuild && make ps` shows all 6 containers healthy after this phase ships. No migration needed.
14. Phase 1-7.6 regression: full suite stays green.

---

## Common pitfalls

1. **`request_id` ContextVar leaks between requests.** Every WSGI request runs in a worker that may handle many requests. ContextVar is per-context but Django middleware needs to RESET the var on entry, not just SET. Use `_request_id_var.set(value)` and capture the returned token; on response, `_request_id_var.reset(token)`. Otherwise stale values survive across requests.

2. **`/metrics` route added before middleware is configured.** Order matters: ensure the route is excluded from logging middleware via path-prefix check (`if request.path == "/metrics": return self.get_response(request)` early-exit pattern).

3. **Pipeline phase timer leaks ContextVar state on exception.** The `timer(phase)` context manager must record the phase duration in a `try/finally` block so failed phases still log timing.

4. **Prometheus metric registration on import.** The `Counter` / `Histogram` objects are module-level singletons. If tests import `apps.core.metrics` multiple times under different settings, you can hit "Duplicated timeseries" errors. Use `metrics.REGISTRY` carefully; tests should clear or use a separate registry.

5. **`prometheus_client.generate_latest()` returns bytes, Django needs str.** Wrap in `HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)` correctly.

6. **gRPC reflection import fails when grpcio-reflection isn't installed.** Don't unconditionally import. Wrap the registration in `try: from grpc_reflection.v1alpha import reflection except ImportError: ...` so production (where reflection is dev-only) doesn't crash.

7. **Signal handler fires inside a thread that can't call `server.stop()`.** Python signals only deliver to the main thread. The gRPC server `wait_for_termination()` is in the main thread; install the handler there. Don't install from a sub-thread.

8. **Graceful shutdown grace=0 (or very small).** Make sure the default is at least a few seconds so warm requests don't get cancelled aggressively. Spec says 10s default.

9. **Load test concurrency hits the gunicorn worker limit.** Compose runs gunicorn with `--workers 2 --timeout 90`. 100 concurrent searches against 2 workers serializes after the first 2. This is *intentional* for v1 (we're not stress-testing capacity, just verifying correctness under concurrency); load test should NOT increase worker count.

10. **structlog processor order matters.** The request-context-enrichment processor must run BEFORE the JSON renderer. If you append it to the end, it adds nothing to the output.

11. **Metric labels with high cardinality.** Spec already bans `tenant_id` / `bot_id`. Watch for sneaky paths: e.g. `endpoint=request.path` would expose the literal URL with UUIDs in it. Use the URL pattern name (`request.resolver_match.url_name`) instead.

12. **Backward-compat test pollutes the search collection.** Use a unique tenant/bot for the test fixture so the deprecated-payload point doesn't show up in other tests' search results.

13. **`/metrics` excluded but still increments counters.** Verify metrics view does NOT increment `qdrant_rag_http_requests_total` for itself (would create a self-feedback loop on every scrape).

---

## Out of scope (deferred to Phase 8b)

- `RUNBOOK.md`
- `scripts/snapshot_qdrant.sh`
- `scripts/backup_postgres.sh`
- `scripts/bootstrap.sh`
- `deploy/qdrant-rag.service` (systemd unit)
- `deploy/nginx/qdrant_rag.conf.example`
- `.github/workflows/ci.yml`
- `Makefile` targets for snapshot/backup/load-test (those tie to scripts in 8b)
- Any change to compose / Dockerfile

## Out of scope (post-v1, never in 8a or 8b)

- Auth (still post-v1)
- TLS termination inside the service (nginx upstream)
- Async ingestion via Celery (wired-but-unused stays so)
- Redis cache layer
- Audit log table
- Quantization
- Per-tenant config
- Multi-host orchestration

---

## Success looks like

- A single `curl http://localhost:8080/metrics` returns Prometheus-formatted metrics with sensible counters and histograms.
- Tail the web logs while doing curls: every HTTP request emits one structured `request_completed` line with `request_id`, timing, and (for pipeline endpoints) per-phase durations.
- `kill -TERM <grpc-pid>` causes the gRPC server to drain and exit cleanly within ~10s.
- `python scripts/load_test.py` reports a green PASS with documented baseline numbers.
- All previously-green tests stay green.
