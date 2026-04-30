# Phase 8a — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_8a_code_hardening/spec.md` — source of truth.
2. `build_prompts/phase_8a_code_hardening/plan.md` — to critique.
3. `build_prompts/phase_7_search_grpc/spec.md` — gRPC server contract; the shutdown handler must respect it.
4. `build_prompts/phase_7_search_grpc/implementation_report.md` — Phase 7 outcomes.
5. `build_prompts/phase_7_5_api_cleanup/implementation_report.md` — Phase 7.5 outcomes (cross-doc_id dedup, server-side hash, removed-field rejection — the access log timing must work with all three pipeline paths).
6. `build_prompts/phase_7_6_raw_payload/implementation_report.md` — Phase 7.6 outcomes (Document.raw_payload).
7. `apps/core/logging.py`, `apps/grpc_service/server.py`, `apps/ingestion/pipeline.py`, `apps/qdrant_core/search.py`, `config/settings.py`, `config/urls.py` — current state.
8. `pyproject.toml` — current dependency list.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_8a_code_hardening/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_8a_code_hardening/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 13 modified/new files addressed?
- All 20 hard constraints addressed (especially: bounded label cardinality, ContextVar reset, structlog processor ordering, gRPC reflection import safety, signal handler in main thread, opt-in subprocess shutdown test, pipeline phase timer non-invasive, `/metrics` excluded from logging)?
- All 14 acceptance criteria mapped to steps?
- All 13 common pitfalls in risk register?
- Out-of-scope (Phase 8b items + post-v1) respected?

### Lens 2 — Edge cases the plan missed

- **gunicorn worker isolation.** Each gunicorn worker has its own ContextVar storage. Plan should confirm this works (it does — ContextVar is per-process). What about thread-pool workers? Django uses sync workers by default. If async views are ever used, ContextVar may not propagate. Spec doesn't mandate async; plan should note this for v2 awareness.

- **Prometheus client multiprocess mode.** With multiple gunicorn workers, each maintains its own metrics registry. Without `prometheus_client.multiprocess.MultiProcessCollector`, scraping any worker shows ONLY that worker's counters. Three options: (A) `prometheus_client.multiprocess` mode (requires shared file dir, more setup), (B) accept per-worker view (only meaningful for relative behavior, not absolute counts), (C) collapse to a single worker for v1. Plan should commit on (B) — accepts v1 limitation, documents in metrics file.

- **gRPC server runs as a SEPARATE process from Django (different container).** The structlog request-context-enrichment and the access-log middleware are Django-only. The gRPC service has its own logging path. Plan should acknowledge: gRPC has its own per-RPC log line + own metric collector; the Django middleware does NOT cover gRPC requests.

- **Docker compose health check on `web` is `curl http://localhost:8000/healthz`.** With the new middleware, `/healthz` will emit `request_completed` log lines on every health probe (every 15s). That adds ~5760 log lines per day per container. Plan should commit: either exclude `/healthz` from the access log (like `/metrics`), or accept the log volume.

- **`/admin/` and `/static/` are now logged too.** Same concern. Recommend exclude `/admin/` (legitimate ops use, but high cardinality of paths) and `/static/` (asset requests). Plan should commit.

- **Path skip list for AccessLogMiddleware.** Recommend an explicit list: `("/metrics", "/healthz", "/static/", "/admin/")`. Spec already excludes `/metrics`; plan should extend.

- **gRPC server's signal handler may conflict with Python's default SIGTERM.** Python's default behavior on SIGTERM is to terminate the process abruptly. Once we register a custom handler, we need to ensure the handler completes (server.stop blocks for grace seconds) before exiting. Plan should commit on the handler implementation (likely: log "shutdown_initiated", call server.stop(grace), log "shutdown_complete", then `sys.exit(0)`).

- **gRPC shutdown grace timing in compose.** `docker compose down` sends SIGTERM, waits 10s by default, then SIGKILL. If `GRPC_SHUTDOWN_GRACE_SECONDS=10` and Compose's stop_grace_period is also 10s, an in-flight request near the boundary gets SIGKILLed. Recommend either lower the app grace to 8s OR raise compose's `stop_grace_period` to 30s. Plan should commit.

- **prometheus-client `make_wsgi_app` vs Django view.** Django's view should call `prometheus_client.generate_latest()` directly and return `HttpResponse`, not mount the WSGI app. Plan should explicit on this.

- **CONTENT_TYPE_LATEST for /metrics response.** Required by Prometheus scraper. Plan should include in the metrics view spec.

- **Access log for non-2xx responses.** Errors (400, 404, 500) should still emit `request_completed`. Plan should confirm middleware path doesn't short-circuit on errors.

- **Test isolation for metrics.** Metrics counters survive across test functions (module-level singletons). pytest may need to reset registry between tests. Plan should specify either: use a separate `CollectorRegistry()` for tests, or `clear()` between tests.

- **gRPC reflection registration order.** `grpc_reflection.v1alpha.reflection.enable_server_reflection(SERVICE_NAMES, server)` must be called AFTER `add_VectorSearchServicer_to_server` but BEFORE `server.start()`. Plan should commit on the order.

- **Backward-compat test isolation in pytest-django.** The fixture creates a Qdrant point with a unique tenant/bot. The DB rollback strategy is per-test transaction — but Qdrant is NOT rolled back. The test must clean up its Qdrant collection in teardown OR use a tenant/bot prefixed with `bc_test_` that's never reused.

- **Load test as a script (not pytest).** Plan should ensure the script can run without Django settings (it's pure HTTP client work), or document that DJANGO_SETTINGS_MODULE is needed if the script imports anything from apps/.

- **`X-Request-ID` header validation.** Should the middleware validate the supplied header is a UUID? Or accept any string? Spec is silent. Recommendation: accept any string up to 100 chars (defensive — clients sometimes use distributed-trace IDs that aren't UUIDs).

### Lens 3 — Production-readiness gaps

- **Structlog processor performance.** Adding a processor to every log call has measurable cost. Mostly negligible but plan should note: this is acceptable in v1; if hot loops emit many logs, profile.

- **Metric collection thread safety.** `prometheus_client` Counters/Histograms are thread-safe. No special handling needed.

- **`/metrics` requires the embedder gauge to be observable.** Setting it from inside the embedder import path means any worker that hasn't loaded BGE-M3 reports 0. Plan should commit: gauge is set to 1 the first time `_get_model()` is called and stays 1 (sticky). NOT set on import.

- **gRPC server stop grace race.** If a Search request takes longer than grace seconds, it's force-cancelled. Search latency is normally <500ms warm. Cold-start search can take 30s+ (BGE-M3 lazy load). Plan should note: post-restart, the FIRST search request can timeout under SIGTERM if grace=10s. Acceptable for v1; document.

### Lens 4 — Pitfall coverage audit

For all 13 spec.md pitfalls. Plan must address each with a concrete preventative step.

### Lens 5 — Sequencing & dependency correctness

Critical sequence:
- `pyproject.toml` + `uv lock` BEFORE rebuild (otherwise prometheus-client missing).
- `apps/core/timing.py` BEFORE `apps/ingestion/pipeline.py` instrumentation.
- Middleware module's ContextVars BEFORE structlog processor that imports them.
- Tests after all source changes settle.
- Stack rebuild AFTER all source changes.

### Lens 6 — Verification command quality

Each verification step: strong / weak rationale.

### Lens 7 — Tooling correctness

- `make run python manage.py check` — uv-like wrapper added during post-7.5 polish.
- `uv lock` (host) — refreshes lock file.
- `make rebuild` — rebuild + restart, keep volumes.
- `grpcurl` — required for reflection smoke test; document install if not present.
- `python scripts/load_test.py` — runs from host or container? Plan should commit on how to invoke.

### Lens 8 — Risk register completeness

- Existing tests calling `apps.core.logging` — verify none break with the new processor.
- Existing tests asserting on log line shape — verify they still match (spec adds keys, doesn't remove).
- Phase 5/6 tests that import pipeline — verify timing import doesn't change behavior.
- Phase 7.5 backward-compat note about old gRPC clients with stale proto — referenced in 8a's backward-compat test.

---

## Output structure

### File 1: `plan_review.md` (NEW)

Standard structure with sections per lens, summary, recommendation.

### File 2: `plan.md` (OVERWRITE)

Same structure as the original. Add section 0: **"Revision notes"** linking to plan_review.md finding numbers. Resolve all [critical] and [major] findings inline.

---

## What "done" looks like

Output to chat:

1. Both files saved.
2. Severity breakdown.
3. Findings escalated.
4. Recommendation.

Then **stop**.
