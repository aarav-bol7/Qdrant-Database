# Phase 7 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_7_search_grpc/spec.md` — source of truth.
2. `build_prompts/phase_7_search_grpc/plan.md` — to critique.
3. `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract.
4. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract.
5. `build_prompts/phase_5b_upload_idempotency/spec.md` — write-path contract (for regression).
6. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
7. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract; pitfall #14a (folded-scalar gotcha).
8. `README.md` — context.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_7_search_grpc/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_7_search_grpc/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 10 deliverables addressed (7 new + 3 modified)?
- All 13 hard constraints addressed (especially #4 algorithm locked, #5 read-only, #11 no reflection)?
- All 10 acceptance criteria mapped to steps?
- All 10 common pitfalls in risk register?
- Out-of-scope respected?

### Lens 2 — Edge cases the plan missed

- **qdrant-client `query_points()` requires named-vector-aware Prefetch.** Spec uses `Prefetch(query=dense_vec, using=DENSE_VECTOR_NAME, ...)`. The actual installed version may use `vector=...` instead of `query=...`. Plan should run the inspection and adapt.
- **`Fusion.RRF` weighted variant location.** Some versions: `FusionQuery(fusion=Fusion.RRF)`. Others have `WeightedFusion`. The 3:1 weighting might require a separate parameter or a different fusion enum. Plan must verify.
- **`compile_proto.sh`'s sed step uses GNU sed `-i` semantics.** On macOS BSD sed, `sed -i` requires an empty argument: `sed -i '' 's/...'`. Compose containers run Linux (GNU sed) — fine. But if a developer runs `compile_proto.sh` on macOS, it'd fail. Plan should accept this v1 limitation (Linux Docker only).
- **`compile_proto.sh` is run by `Dockerfile RUN` BUT the .proto file is COPIED in by `COPY . .`.** The RUN step must come AFTER the COPY. Plan must verify the order in the Dockerfile change.
- **Server.py imports `apps.tenants.validators` via the search.py chain.** `apps.tenants.validators` imports `django.core.validators.RegexValidator`. Without `django.setup()`, this might raise. The order: `django.setup()` BEFORE any `apps.*` imports. Plan must verify.
- **Test `test_search_returns_relevant_chunks` may flake** if BGE-M3 + small fixture doesn't reliably score above 0.65. The test should NOT assert chunks > 0 — just that the response shape is valid. Plan should soften this assertion.
- **Test fixture `uploaded_doc` triggers Phase 5's full pipeline, which loads BGE-M3 in the WEB container.** Then `search_stub.Search(...)` triggers BGE-M3 load in the GRPC container. Two separate loads × ~30s. First test in a session pays ~60s. Plan should warn.
- **`search_pb2_grpc.py` import path.** After the sed fix, `from . import search_pb2`. But the test file imports `from apps.grpc_service.generated import search_pb2_grpc` — that's correct. Plan should confirm the import works after the sed fix.
- **`gRPC.channel_ready_future(channel).result(timeout=5)`** — what if the channel CAN connect but the server isn't actually serving the SearchService yet (e.g., in mid-startup)? channel_ready_future succeeds on TCP connect, not on service readiness. A subsequent Search call might still fail. Plan can either add a HealthCheck call in the fixture OR accept that v1 uses a 5s sleep + channel ready as good enough.
- **`Chunk.page_number = 0` ambiguity.** Proto3 int32 default = 0; clients can't distinguish "page 0" (uncommon) from "no page". Document. Or use `optional int32 page_number = 9` with proto3 field presence. v1: int32 with 0=absent convention.
- **Server logs contain `query_length`** but not the query string. Confirm no PII leak.

### Lens 3 — Production-readiness gaps

- **Server graceful shutdown.** SIGTERM handler exists in spec, but what about SIGINT (Ctrl-C in dev)? Add both signals.
- **gRPC server doesn't restart workers.** It's a single Python process with a thread pool. If the process dies (OOM, segfault from torch on bad input), the container restarts via Compose's `restart: unless-stopped`. Plan should verify this restart policy is preserved.
- **No prometheus metrics for search.** Phase 8 adds metrics; flag for then.
- **gRPC max_message_length defaults.** A search response with top_k=20 chunks × ~1 KB text ≈ 20 KB. Well under default 4 MB. No issue.
- **No HealthCheck mid-call.** A long-running Search (slow embed on cold worker, ~30s) blocks the thread. The gRPC server's other RPCs queue. v1 acceptable.
- **No structured query logging at WARNING for slow queries.** A search that takes >2s is suspicious. Plan should add a threshold-based log.
- **The grpc container's Compose healthcheck.** Spec says no healthcheck. But `restart: unless-stopped` + dependencies means the container can restart-loop if startup fails. Plan should accept v1; Phase 8 adds a proper healthcheck.

### Lens 4 — Pitfall coverage audit

For all 10 spec.md pitfalls:
1. Plan addresses?
2. Verification catches?

### Lens 5 — Sequencing & dependency correctness

Critical sequence:
- search.proto first.
- compile_proto.sh fill-in second (must work before stubs can be imported).
- Generated stubs import-test third.
- API verification step (NEW — before search.py).
- search.py fourth (uses verified API).
- handler.py fifth (uses search.py + stubs).
- server.py sixth (uses handler).
- Dockerfile + docker-compose.yml together.
- verify_setup.py extension.
- Tests last.

### Lens 6 — Verification command quality

- After `compile_proto.sh`: import test of generated stubs is strong.
- After `query_points` API inspection: print signature → strong (catches drift early).
- Manual `grpcurl` smoke covering 4 error paths: strong.
- pytest test_search_query.py (mocked): strong.
- pytest test_search_grpc.py (real stack): strong.
- Phase 1-6 regression: full suite + `make health`. Strong.

### Lens 7 — Tooling correctness

- `python -m grpc_tools.protoc` invocation: standard.
- `make up` (Compose v2). Standard.
- `grpcurl` is a separate binary (not from pip). Plan should note: if not installed, a Python equivalent works (`uv run python -m grpc_tools.protoc ...` only generates; not a client). Use the Python grpc client in tests; the manual smoke (criterion 6) requires `grpcurl` OR a Python REPL alternative. Plan should provide both.
- Test runs from inside container: `docker compose exec web pytest`.

### Lens 8 — Risk register completeness

- Two BGE-M3 instances (one in web container, one in grpc container) exceed 8 GB box budget if both run concurrently with full activity. Plan should flag.
- gRPC clients (DynamicADK, etc.) caching the channel — if the server restarts mid-session, clients see UNAVAILABLE. Plan should flag for v2.
- search.proto field numbering — once shipped, removing fields is a breaking change. v1 doesn't worry about this; document.

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
