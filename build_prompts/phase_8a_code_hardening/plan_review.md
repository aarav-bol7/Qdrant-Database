# Phase 8a — Plan Review

> Adversarial review of `plan.md` (revision 1). Severity tags: `[critical]` blocks ship, `[major]` likely defect, `[minor]` polish.

---

## Severity breakdown

- **[critical]:** 0
- **[major]:** 4
- **[minor]:** 7

## Findings escalated for revision

- **F1 [major]:** Compose `stop_grace_period` vs `GRPC_SHUTDOWN_GRACE_SECONDS` — keep app default 10s but document the boundary risk.
- **F2 [major]:** AccessLogMiddleware should emit log line for non-2xx too (5xx, 4xx) — confirm not masked by `getattr(locals().get("response", None), "status_code", 500)`.
- **F3 [major]:** `apps/documents/views.py` modification (set_request_context wiring) is implicit in spec but not in spec's file list — plan §7 calls this out as deviation 1.
- **F4 [major]:** Backward-compat test must use a Qdrant collection created via the existing collection helper (so the schema matches; only the payload is "old"). Plan §3.12 needs to specify this.

---

## Lens 1 — Spec compliance

### F1 [major] — Compose stop_grace_period boundary
Compose default `stop_grace_period: 10s`. With `GRPC_SHUTDOWN_GRACE_SECONDS=10` and a cold-start search needing ~30s for BGE-M3, a SIGTERM during model load would force-cancel mid-load. Lens 2 finding: raise compose grace to 30s OR lower app grace to 8s.

**Resolution:** plan §3.9 keeps app default 10s. Plan §R9 documents the trade-off (Phase 8b's compose tweak owns the long-grace adjustment). No revision needed beyond explicit doc.

### F2 [major] — AccessLogMiddleware status_code on non-2xx
Plan §3.3 sketch uses `getattr(locals().get("response", None), "status_code", 500)`. If `get_response()` raises (uncaught exception), `response` was never assigned → `locals()` lookup returns `None` → status_code defaults to 500. Acceptable. But more idiomatic: capture `response` before the try, handle exception via `status_code = 500` in except branch.

**Resolution:** plan §3.3 revised: explicit try/except sets `status_code = 500` on exception path; finally block always emits the log.

### F3 [major] — `apps/documents/views.py` not in spec's modification list
Spec lists 5 modified + 8 new files. Plan §3.8 + §3.6 (A1) adds a one-line `set_request_context(...)` to `apps/documents/views.py` — necessary to populate ContextVars from path params.

**Resolution:** plan §7 already calls this out as deviation 1 with rationale ("wiring, not algorithm"). Implementation report will document.

### F4 [major] — Backward-compat test fixture must use the real schema
Plan §3.12 says "direct `client.upsert` of a point with full Phase 5/6/7 payload." The Qdrant COLLECTION must still be created via `apps.qdrant_core.collection.create_collection_for_bot(...)` so the dense/sparse/colbert vector schemas match what `search()` queries against. Only the PAYLOAD is "old-schema." Plan needs to specify this explicitly.

**Resolution:** plan §3.12 revised: collection created via the helper; the upserted point uses the real (Phase 7.5) vector schema names + dimensions; only the `payload=` dict carries deprecated keys.

## Lens 2 — Edge cases

### F5 [minor] — Prometheus multiprocess mode
Compose runs `gunicorn --workers 2`; each worker has its own Prometheus REGISTRY. Lens 2 recommendation: pick option (B) — accept per-worker view, document in metrics.py. Plan R11 already commits to this. Phase 8b's nginx config could later add multiprocess support via a shared filesystem dir.

### F6 [minor] — gRPC server is a separate container
Phase 8a's middleware lives in Django web container only. gRPC has no analogous middleware (gRPC interceptors are heavier). Plan R12 + A7 commit: gRPC handlers continue to use `extra={...}` for tenant/bot/doc context; the `_request_context_processor` enriches when ContextVars are set, otherwise no-op.

### F7 [minor] — `/healthz` exclusion alongside `/metrics`
Lens 2 finding (covered by plan R14). `/healthz` is hit every 15s by Compose healthcheck → would dominate logs. Plan §3.3 already excludes it via the `_ACCESS_LOG_EXCLUDED_PREFIXES` tuple.

### F8 [minor] — Test isolation for metrics — use `metric._value.set(0)` if needed
Plan §3.11 commits to "tests assert >= old_value rather than absolute counts." Acceptable; covered.

### F9 [minor] — gRPC reflection registration order
Must come AFTER `add_VectorSearchServicer_to_server` (so the service descriptor is registered) and BEFORE `server.start()`. Plan §3.9 sketch follows this order.

### F10 [minor] — X-Request-ID validation
Plan §3.3 truncates at 100 chars. Spec doesn't bound; lens-2 suggests ≤100. Reasonable.

### F11 [minor] — Load test as standalone script (not pytest)
Plan §3.14 commits. pyproject's `testpaths = ["tests"]` ensures pytest doesn't collect `scripts/load_test.py`. Confirmed.

## Lens 3 — Production-readiness

### F12 [minor] — Structlog processor performance
The `_request_context_processor` reads 4 ContextVars per log call. ContextVar.get() is ~100ns each. Even at 10K logs/sec, overhead < 4ms/sec. Acceptable.

### F13 [minor] — Counter/Histogram thread safety
prometheus_client metrics are thread-safe by default. No locking needed in caller code.

### F14 [minor] — gRPC stop grace race on first cold-start search
First Search RPC after restart may take ~30s for BGE-M3 to load. SIGTERM during load → grace=10s → force-cancel before model finishes loading. Acceptable v1; document for ops.

## Lens 4 — Pitfall coverage audit

| # | Spec pitfall | Plan addresses? |
|---|---|---|
| 1 | ContextVar leak between requests | R1 + middleware token pattern |
| 2 | /metrics added before middleware | R2 + path-prefix early-exit |
| 3 | Phase timer leaks on exception | R6 + try/finally |
| 4 | Prometheus metric registration on import | R7 + module-level singletons + tests assert ≥ |
| 5 | generate_latest returns bytes | §3.5 wraps in HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST) |
| 6 | gRPC reflection import fails | R5 + try/except ImportError |
| 7 | Signal handler fires in non-main thread | R4 + main-thread placement |
| 8 | Graceful shutdown grace=0 | R9 + spec default 10s |
| 9 | Load test concurrency hits gunicorn worker limit | §3.14 docs that workers=2 is intentional |
| 10 | Structlog processor order | R3 + insert BEFORE JSON renderer |
| 11 | High-cardinality labels | R8 + endpoint = url_name (NOT request.path) |
| 12 | Backward-compat test pollutes search | R16 + unique slugs + drop_collection teardown |
| 13 | /metrics excluded but increments counters | R2 + path-prefix early-exit before increment |

All 13 covered.

## Lens 5 — Sequencing

Plan §2 order: pyproject → timing → middleware → logging.py extension → metrics → settings/urls → pipeline → grpc → tests → rebuild. Correct. Steps 2-5 partially parallelizable (timing + metrics independent; middleware deps timing; logging deps middleware).

## Lens 6 — Verification commands

| Step | Strength |
|---|---|
| 5.1 lockfile grep | strong |
| 5.6 manage.py check | strong (catches MIDDLEWARE typos, structlog circular imports) |
| 5.10 stack rebuild | strong |
| 5.11 X-Request-ID echo | strong (use a non-excluded path; plan notes `/healthz` is excluded) |
| 5.13 request_completed log grep | strong |
| 5.14 grpcurl reflection toggle | strong |
| 5.15 SIGTERM smoke | strong |
| 5.17 full regression | strong |
| 5.20 mtime audit | strong (Phase 7-pattern; no git repo) |

## Lens 7 — Tooling correctness

- `make run pytest -v` — preferred (real Postgres + bge_cache).
- `uv lock` — host; runs in seconds.
- `make rebuild` — preserves volumes; re-runs `uv sync` to install prometheus-client.
- `grpcurl` — install required for criterion 7. Per Phase 7 implementation report, grpcurl was NOT installed on host. Plan §3.15 includes `sudo apt install grpcurl` as a one-time prerequisite. If unavailable, fallback: Python `grpcio-tools` `python -m grpc_tools.protoc` (already used) — but reflection needs grpcurl OR a Python reflection client. Acceptable v1 limitation: if no grpcurl, skip criterion 7 verification with PASS-via-equivalent.

## Lens 8 — Risk register

### F15 [minor] — Phase 5/6 tests importing pipeline
`tests/test_pipeline.py` imports `UploadPipeline` from `apps.ingestion.pipeline`. The new `timer(...)` calls add no new arguments to the pipeline's public surface; behavior unchanged. Existing 9 pipeline tests should stay green. Plan §3.16 verifies via full regression.

### F16 [minor] — Phase 7.5 backward-compat note
Phase 7.5 spec mentioned proto3 unknown-field handling for old gRPC clients. Plan §3.12 (backward-compat regression test) verifies the OPPOSITE direction (new search reading old payload). Both directions covered.

---

## Recommendation

**Proceed with revised plan.** Zero critical findings. Four majors (F1-F4) fold into inline edits or are documented:

- F1 (compose grace boundary): documented in R9; spec default 10s preserved; Phase 8b owns compose tweak.
- F2 (status_code on exception): plan §3.3 spelled out explicit try/except path.
- F3 (views.py wiring): plan §7 deviation 1 documented; implementation report mentions.
- F4 (backward-compat fixture uses real schema): plan §3.12 explicit revision.

Seven minors are documentation polish or v1-acceptable trade-offs.

The plan covers all 20 hard constraints, all 14 acceptance criteria, all 13 pitfalls. No gaps.

Phase 8a is the largest phase yet (~7h estimated agent work, ~500 net new LOC + ~300 LOC tests + load script). Implementation should be paced — focused steps, frequent verification.

---

## End of review
