# Phase 7 — Implementation Plan (revised)

> Produced by Prompt 1 (PLAN), revised by Prompt 2 (REVIEW). Inputs: `spec.md` (read in full), Phases 1-6 specs + reports, qdrant-client 1.17.1 API inspection, `plan_review.md`.

---

## 0. Revision notes

This plan is revision 2. Findings from `plan_review.md` resolved inline:

- **F1 [critical]:** Added a runtime verification step inside step 3.6 that confirms the 3× duplication trick yields the spec's 3:1 weighting on the installed Qdrant. Plus a fallback to plain `Fusion.RRF` (without weighting) if the trick fails, documented as a v2-deferred deviation.
- **F2 [critical]:** Added a `[minor]` note documenting that the spec's `score_threshold=0.65` is empirical and may need per-workload tuning post-Phase 8 metrics; plan honors the spec value as-is.
- **F3 [major]:** Added a `grep -r "reflection" apps/grpc_service/` check to step 3.8.
- **F4 [major]:** Dropped the `uploaded_doc` fixture and `test_search_returns_relevant_chunks` from the integration test plan. Live-data search testing is deferred to a Phase 7 follow-on / Phase 8 ship gate. Step 3.13 now ships only validation/NOT_FOUND/HealthCheck/cross-tenant-isolation tests.
- **F5 [major]:** Spelled out the docker-compose.yml diff in §3.10 line-by-line: ONLY the 1-line `command:` field changes; all 9 other fields preserved verbatim.
- **F6 [minor]:** Added a `pyproject.toml` + `uv.lock` mtime/diff check to §3.16.
- **F7 [major]:** Added `TestSearchEmptyResults` to the unit test plan (§3.12).
- **F8 [major]:** Added explicit `with_vectors=False` to the `query_points()` call (§3.6).
- **F13 [minor]:** Added a `add_insecure_port` return-value check in server.py (§3.8).
- **F19 [minor]:** Added a version-string assertion to `make health` verification (§3.14).
- **F23 [minor]:** Added a `// Field numbers are stable; do not renumber.` comment to proto/search.proto (§3.1).

The 8 lenses' coverage is folded into the revised plan; explicit cross-references appear in the relevant sections.

---

## 1. Plan summary

Phase 7 wires the gRPC read path on top of the verified write path of Phases 1-6. The build is small in raw line count but spans more files than any prior phase: 7 new (proto + 4 grpc/qdrant_core source files + 2 test files) plus 4 modified (Dockerfile, docker-compose.yml, scripts/compile_proto.sh, scripts/verify_setup.py). The riskiest area is the qdrant-client `query_points()` call shape — the spec sketches a flat 2-prefetch + colbert rerank shape, but the locked algorithm requires a fusion stage and qdrant-client 1.17.1 has no Weighted RRF (only plain `Fusion.RRF`). The plan resolves this by nesting the dense + sparse prefetches inside an outer Prefetch whose `query=FusionQuery(fusion=Fusion.RRF)`, and emulating the 3:1 dense:sparse weight by **duplicating the dense prefetch 3×** so plain RRF input frequency yields the locked 3:1 effective weighting (with a runtime verification step + fallback). The build verifies itself via: (a) compile-time stub import test, (b) qdrant-client API inspection (already run — see §6), (c) `manage.py check`, (d) Docker stack rebuild + `make health` (asserting version), (e) manual `grpcurl` smoke covering 4 error paths, (f) unit tests with mocked Qdrant including an empty-results assertion, (g) integration tests against the real gRPC + Qdrant (validation + NOT_FOUND + HealthCheck + cross-tenant only — live-data search is manual via verify_setup), (h) Phase 1-6 regression of all 118+ prior tests.

---

## 2. Build order & dependency graph

The dependency chain is strictly linear except where parallelism is explicit. Earlier rows must be done before later rows.

| # | Artifact | Depends on | Why |
|---|---|---|---|
| 1 | `proto/search.proto` | — | No code deps; defines the wire format. |
| 2 | `scripts/compile_proto.sh` (fill in) | 1 | Reads the .proto. |
| 3 | `apps/grpc_service/generated/__init__.py` (empty package marker) | — | Independent of 1+2 but must exist before 4. |
| 4 | `bash scripts/compile_proto.sh` (run locally) | 1, 2, 3 | Generates `search_pb2.py` + `search_pb2_grpc.py` + applies the sed fix. |
| 5 | qdrant-client API inspection | — | Independent. **Already done in this plan §6.** |
| 6 | `apps/qdrant_core/search.py` | 5 | Uses the verified API. |
| 7 | `apps/grpc_service/handler.py` | 4 (stubs), 6 (search.py) | Imports both. |
| 8 | `apps/grpc_service/server.py` | 7 (handler) | Wires the service into a gRPC server. |
| 9 | `Dockerfile` modification | 1, 2 | Adds `RUN bash scripts/compile_proto.sh` after `COPY . .`. |
| 10 | `docker-compose.yml` modification | 8 | Replaces grpc service `command:` with the server invocation. |
| 11 | `scripts/verify_setup.py` extension | 4, 8 | Uses generated stubs to issue a Search RPC. |
| 12 | `tests/test_search_query.py` | 6 | Unit tests against `search()` with mocked client. |
| 13 | `tests/test_search_grpc.py` | 4, 10 (live server) | Integration tests against a running gRPC server. |
| 14 | Docker stack rebuild + smoke | 9, 10 | Whole-stack validation. Both 9 AND 10 must be saved BEFORE rebuild. |
| 15 | Phase 1-6 regression | 14 | Final correctness check. |

Notes:
- Steps 1, 3, 5 can run in parallel.
- Step 4 (compile_proto.sh local run) is repeated automatically inside the image build via step 9, but local generation is needed so the human can read the stubs while writing handler.py.
- Step 6 cannot start until step 5's findings are documented; the spec sketch's call shape needs adapting (see §6).
- Step 14 requires BOTH steps 9 and 10 to be on disk; rebuilding before Compose is updated would still pick up the new command on next start, but it's cleaner to save both, then rebuild.

---

## 3. Build steps (sequenced)

### Step 3.1 — Write `proto/search.proto`

- **Goal:** Fix the wire format.
- **Files:** `proto/search.proto` (NEW).
- **Content:** Verbatim from spec.md §"File-by-file specification → proto/search.proto". Add a leading comment block:
  ```
  // qdrant_rag.v1 search service.
  // Field numbers are stable across releases. Do not renumber or remove fields
  // without bumping the package version (qdrant_rag.v1 -> qdrant_rag.v2).
  ```
- **Verification:** `uv run python -c "import grpc_tools; print('ok')"` — Phase 1 already pinned grpcio-tools.
- **Rollback:** `rm proto/search.proto`.
- **Estimated effort:** 5 min.

### Step 3.2 — Fill in `scripts/compile_proto.sh`

- **Goal:** Replace Phase 1's stub with a working protoc invocation.
- **Files:** `scripts/compile_proto.sh` (MODIFY — currently a no-op echo).
- **Content:** Verbatim from spec.md, including the GNU-sed fix that rewrites `import search_pb2` to `from . import search_pb2`.
- **Verification:** `chmod +x scripts/compile_proto.sh && bash scripts/compile_proto.sh`. Expect exit 0 and a "[compile_proto] Stubs generated" message.
- **Rollback:** Restore Phase 1's echo stub.
- **Estimated effort:** 5 min.

### Step 3.3 — Create empty `apps/grpc_service/generated/__init__.py`

- **Goal:** Make the generated/ directory a Python package.
- **Files:** `apps/grpc_service/generated/__init__.py` (NEW — empty).
- **Verification:** `python -c "import apps.grpc_service.generated"` (after step 3.4 generates the .py files).
- **Rollback:** `rm` it.
- **Estimated effort:** 1 min.

### Step 3.4 — Generate stubs locally

- **Goal:** Produce `search_pb2.py` + `search_pb2_grpc.py` so the developer can inspect their public surface while writing handler.py.
- **Files:** `apps/grpc_service/generated/search_pb2.py` + `search_pb2_grpc.py` (GENERATED, gitignored).
- **Verification:**
  ```
  uv run python -c "from apps.grpc_service.generated import search_pb2, search_pb2_grpc; print('ok')"
  ```
  Must print "ok". If the import fails with `ModuleNotFoundError: No module named 'search_pb2'`, the sed fix didn't apply — manually fix:
  ```
  sed -i 's/^import search_pb2/from . import search_pb2/' apps/grpc_service/generated/search_pb2_grpc.py
  ```
- **Rollback:** `rm apps/grpc_service/generated/search_pb2*.py`.
- **Estimated effort:** 2 min.

### Step 3.5 — qdrant-client API inspection (already complete; results below)

- **Goal:** Verify the call shape for prefetch + fusion + multivector rerank in qdrant-client 1.17.1 BEFORE writing search.py.
- **Files:** none (read-only inspection).
- **Findings:** see §6.
- **Effort:** done.

### Step 3.6 — Write `apps/qdrant_core/search.py`

- **Goal:** Pure logic for the locked hybrid query.
- **Files:** `apps/qdrant_core/search.py` (NEW).
- **Adaptations from spec sketch (driven by §6):**
  1. Replace flat `prefetch=[dense_pf, sparse_pf]` with **nested** structure:
     ```python
     fusion_node = Prefetch(
         prefetch=[
             Prefetch(query=dense_vec, using=DENSE_VECTOR_NAME, limit=PREFETCH_LIMIT, filter=qfilter),  # × RRF_DENSE_WEIGHT
             Prefetch(query=dense_vec, using=DENSE_VECTOR_NAME, limit=PREFETCH_LIMIT, filter=qfilter),
             Prefetch(query=dense_vec, using=DENSE_VECTOR_NAME, limit=PREFETCH_LIMIT, filter=qfilter),
             Prefetch(query=sparse_vec, using=SPARSE_VECTOR_NAME, limit=PREFETCH_LIMIT, filter=qfilter),  # × RRF_SPARSE_WEIGHT
         ],
         query=FusionQuery(fusion=Fusion.RRF),
         limit=FUSION_LIMIT,
     )
     response = client.query_points(
         collection_name=name,
         prefetch=[fusion_node],
         query=colbert_vec,                        # rerank
         using=COLBERT_VECTOR_NAME,
         limit=top_k,
         score_threshold=SCORE_THRESHOLD,
         with_payload=True,
         with_vectors=False,                       # explicit; default but defensive (F8)
         query_filter=qfilter,
     )
     ```
  2. Keep the `is_active=true` filter inside every leaf prefetch AND on the final query (`query_filter=qfilter`).
  3. Keep `with_retry()` decorator on `_execute_query`.
  4. Constants:
     - `DEFAULT_TOP_K = 5`
     - `MAX_TOP_K = 20`
     - `SCORE_THRESHOLD = 0.65` (spec value; may need tuning post-Phase 8 — F2 minor)
     - `PREFETCH_LIMIT = 50`
     - `FUSION_LIMIT = 100` (candidates after fusion to feed into rerank)
     - `RRF_DENSE_WEIGHT = 3` (number of dense Prefetch duplicates)
     - `RRF_SPARSE_WEIGHT = 1` (number of sparse Prefetch instances)
  5. Use `from apps.qdrant_core.naming import collection_name` (NOT from `collection.py`; per Phase 2/3 layout).
  6. Define `class CollectionNotFoundError(QdrantOperationError)` so the handler can map it to NOT_FOUND.
  7. Helper `_build_prefetch_list(dense_vec, sparse_qd, qfilter)` returns the inner-prefetch list, generated dynamically via `[Prefetch(...) for _ in range(RRF_DENSE_WEIGHT)] + [Prefetch(<sparse>)]` so the duplication count is data-driven (one constant change adjusts the weighting).
- **Runtime weighting verification (F1 [critical] resolution):** during the implementation phase, BEFORE running the full test suite, the implementer runs an ad-hoc verification against a live Qdrant collection:
  1. Create a throwaway collection.
  2. Insert two points: P_dense ranks high on dense (e.g., a vector aligned with the query's dense embedding), P_sparse ranks high on sparse (e.g., a payload string with a unique high-IDF token matching the query).
  3. Issue the locked query with `RRF_DENSE_WEIGHT=3, RRF_SPARSE_WEIGHT=1`.
  4. Verify P_dense's score > P_sparse's score by approximately 3× (allowing for rerank reshuffling — the rerank stage acts on both, so exact 3:1 is impossible to assert; instead assert P_dense ranks above P_sparse and the score gap is non-trivial).
  5. Issue with `RRF_DENSE_WEIGHT=1, RRF_SPARSE_WEIGHT=3`. Verify the order flips. This proves duplication has effect.
  6. If verification fails (Qdrant deduplicates input prefetches), fall back to plain RRF with `[D, S]` (single dense + single sparse), accept the 1:1 weighting deviation, document in implementation_report.md as a known v2-deferred deviation.
- **Verification:**
  ```
  uv run python -c "from apps.qdrant_core.search import search, CollectionNotFoundError, DEFAULT_TOP_K, MAX_TOP_K, SCORE_THRESHOLD, PREFETCH_LIMIT, FUSION_LIMIT, RRF_DENSE_WEIGHT, RRF_SPARSE_WEIGHT; print('ok')"
  uv run python manage.py check
  ```
- **Rollback:** `rm apps/qdrant_core/search.py`.
- **Estimated effort:** 30 min source code + 15 min weighting verification = 45 min.

### Step 3.7 — Write `apps/grpc_service/handler.py`

- **Goal:** SearchService implementation.
- **Files:** `apps/grpc_service/handler.py` (NEW).
- **Content:** Verbatim from spec.md, with these checks:
  - `from apps.grpc_service.generated import search_pb2, search_pb2_grpc` — must work because the sed fix made the import package-relative.
  - Validation order: slug → query non-empty → top_k → only_active.
  - Map exceptions: `CollectionNotFoundError` → NOT_FOUND, `QdrantConnectionError` → UNAVAILABLE, `QdrantError` → INTERNAL, `Exception` → INTERNAL.
  - HealthCheck reads `_get_model.cache_info().currsize > 0` from `apps.ingestion.embedder` — Phase 4's private function. Acceptable cross-module dependency for v1; flagged in §6 as ambiguity.
  - VERSION = "0.1.0-dev" (matches `apps/core/views.py`'s healthz response per Phase 1 — assert via step 5.12).
  - Empty chunks return is OK (handler always returns `SearchResponse`; never NOT_FOUND/INVALID_ARGUMENT for empty results — F7 minor).
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.grpc_service.handler import VectorSearchService, VERSION
  print('ok, version=', VERSION)
  "
  ```
- **Rollback:** `rm apps/grpc_service/handler.py`.
- **Estimated effort:** 25 min.

### Step 3.8 — Write `apps/grpc_service/server.py`

- **Goal:** gRPC server entrypoint.
- **Files:** `apps/grpc_service/server.py` (NEW).
- **CRITICAL ordering:** `django.setup()` must come BEFORE any `apps.*` import. server.py first imports stdlib (`os, signal, logging`) and `django, grpc`; THEN `os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); django.setup()`; THEN `from apps.grpc_service.generated import search_pb2_grpc; from apps.grpc_service.handler import VectorSearchService` (with `# noqa: E402` to silence ruff's import-order rule).
- **Bind verification (F13 minor):**
  ```python
  bound_port = server.add_insecure_port(bind_addr)
  if not bound_port:
      raise SystemExit(f"grpc_server bind failed: {bind_addr}")
  logger.info("grpc_server_bound", extra={"port": bound_port})
  ```
- **Reflection guard (F3 major):** the source must NOT import `grpc_reflection.v1alpha.reflection`. Verify with `grep -r "reflection" apps/grpc_service/` after the file is written; expect no matches.
- **Both signal handlers (R8):** SIGTERM + SIGINT both call `_shutdown(signum, frame)` which logs and stops the server with grace period.
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.grpc_service.server import serve, DEFAULT_PORT, DEFAULT_MAX_WORKERS, GRACEFUL_SHUTDOWN_S
  print('ok, port=', DEFAULT_PORT, 'workers=', DEFAULT_MAX_WORKERS, 'grace=', GRACEFUL_SHUTDOWN_S)
  "
  grep -r "reflection" apps/grpc_service/   # expect no output
  ```
- **Rollback:** `rm apps/grpc_service/server.py`.
- **Estimated effort:** 15 min.

### Step 3.9 — Modify `Dockerfile`

- **Goal:** Bake the generated stubs into the image.
- **Files:** `Dockerfile` (MODIFY).
- **Diff:** Insert ONE line in the **builder stage**, AFTER the `RUN ... uv sync ...` on line 17 (the second uv sync after `COPY . .`). New line:
  ```
  RUN bash scripts/compile_proto.sh
  ```
  Placement: between line 17 (`uv sync --frozen --no-dev || uv sync --no-dev`) and line 19 (the `# Runtime stage` comment). The runtime stage's `COPY --from=builder /app /app` then picks up the generated files at `/app/apps/grpc_service/generated/`.
- **Why builder stage:** the runtime stage is `python:3.13-slim` with only `libpq5` + `curl` apt-installed and uv copied in; running compile_proto in runtime would re-resolve grpcio-tools. Generating in the builder uses the already-installed venv.
- **Verification:**
  ```
  docker compose -f docker-compose.yml build       # full rebuild expected (cache busted)
  ```
  And after rebuild + stack up:
  ```
  docker compose -f docker-compose.yml exec web python -c "from apps.grpc_service.generated import search_pb2; print('ok')"
  ```
- **Rollback:** Revert the inserted RUN line.
- **Estimated effort:** 10 min + ~5-15 min image rebuild.

### Step 3.10 — Modify `docker-compose.yml`

- **Goal:** Replace the grpc service's `sleep infinity` with the real server invocation.
- **Files:** `docker-compose.yml` (MODIFY).
- **Diff (line-by-line, F5 major resolution):**
  ```
  Lines 87-99 (current grpc service block):
      grpc:                                                      # KEEP
        build: .                                                 # KEEP
        container_name: qdrant_rag_grpc                          # KEEP
        command: sh -c "echo 'gRPC service not implemented yet (Phase 7).' && sleep infinity"   # REPLACE
        env_file: .env                                           # KEEP
        depends_on:                                              # KEEP
          web: {condition: service_healthy}                      # KEEP
        ports:                                                   # KEEP
          - "${GRPC_PORT:-50051}:50051"                          # KEEP
        volumes:                                                 # KEEP
          - bge_cache:/app/.cache/bge                            # KEEP
        restart: unless-stopped                                  # KEEP
        networks: [qdrant_rag_net]                               # KEEP
  ```
  Replace ONLY the single-line `command:` (line 90) with the YAML list-form per Phase 1 pitfall #14a:
  ```yaml
      command:
        - sh
        - -c
        - >-
            python -m apps.grpc_service.server
  ```
  All other 9 fields (build, container_name, env_file, depends_on, ports, volumes, restart, networks, plus the service-name `grpc:` itself) are preserved byte-identical.
- **Verification:**
  ```
  docker compose -f docker-compose.yml config | sed -n '/^  grpc:/,/^  [a-z]/p'
  ```
  Confirm the rendered config shows `command: [sh, -c, "python -m apps.grpc_service.server"]` (or YAML-equivalent), with NO surviving `sleep infinity`. Also confirm `restart: unless-stopped` and `depends_on.web.condition: service_healthy` are still rendered.
- **Rollback:** Restore the original `command:` line.
- **Estimated effort:** 5 min.

### Step 3.11 — Extend `scripts/verify_setup.py`

- **Goal:** `--full` mode runs a Search RPC round-trip.
- **Files:** `scripts/verify_setup.py` (EXTEND).
- **Content:** Add `_search_roundtrip()` function (returning `tuple[bool, str]` to match the existing idiom — see Phase 3 deviation 3). The function:
  1. Connects to `f"{os.environ.get('GRPC_HOST', 'localhost')}:{os.environ.get('GRPC_PORT', '50051')}"`.
  2. Issues HealthCheck — expects `qdrant_ok=True` (best-effort; embedder_loaded may be false on first run).
  3. Issues Search with empty query → expects INVALID_ARGUMENT.
  4. Returns `(True, "ok")` on success.
- **WHY GRPC_HOST=localhost:** when run from host shell, `localhost:50051` works. When run inside the web container, it'd need `GRPC_HOST=grpc` (Compose service-name DNS).
- **Wire `--full`:** call `_search_roundtrip()` after `_warmup_embedder()`. If it returns `(False, msg)`, exit 1 with `[verify_setup] FAIL search: {msg}`.
- **Verification:**
  ```
  uv run python scripts/verify_setup.py --help     # parses args
  uv run python -c "from apps.grpc_service.generated import search_pb2_grpc; print('ok')"   # F18: stubs importable
  ```
- **Rollback:** Revert to Phase 4's verify_setup.py.
- **Estimated effort:** 15 min.

### Step 3.12 — Write `tests/test_search_query.py`

- **Goal:** Unit-test `apps.qdrant_core.search.search()` with mocked Qdrant client + mocked embedder.
- **Files:** `tests/test_search_query.py` (NEW).
- **Test classes:**
  1. **`TestSearchHappyPath`** — happy-path call shape.
     - `test_calls_qdrant_with_correct_collection_name`
     - `test_uses_default_top_k`
     - `test_score_threshold_is_065`
     - `test_with_vectors_is_false` (F8 resolution)
     - `test_query_filter_includes_is_active_true`
     - `test_top_level_prefetch_has_one_outer_node` (the nested fusion node)
     - `test_inner_prefetches_have_3_dense_and_1_sparse` (R2 + F1: assert duplication shape)
     - `test_inner_prefetches_use_correct_vector_names` (DENSE_VECTOR_NAME × 3 + SPARSE_VECTOR_NAME × 1)
     - `test_inner_prefetches_have_filter_with_is_active_true` (R4)
     - `test_fusion_node_uses_rrf` (FusionQuery(fusion=Fusion.RRF))
     - `test_fusion_node_limit_is_100` (FUSION_LIMIT)
     - `test_final_query_uses_colbert_vector_name` (rerank)
  2. **`TestCollectionNotFound`** — collection_exists=False → CollectionNotFoundError raised.
  3. **`TestSearchEmptyResults`** (F7 resolution) — query_points returns `points=[]` → `result["chunks"] == []`, `total_candidates == 0`, `threshold_used == 0.65`.
  4. **`TestFilterComposition`** — adding source_types/tags/category produces the expected MatchAny / MatchValue conditions.
- **Setup:** `mock_deps` fixture patches `get_qdrant_client` and `embed_query` with MagicMock returning known values.
- **Verification:**
  ```
  uv run pytest tests/test_search_query.py -v
  ```
  Expected: green in <5s (no model load).
- **Rollback:** `rm tests/test_search_query.py`.
- **Estimated effort:** 30 min (more tests than spec sketch).

### Step 3.13 — Write `tests/test_search_grpc.py`

- **Goal:** Integration tests via a Python gRPC client.
- **Files:** `tests/test_search_grpc.py` (NEW).
- **Test classes (F4 resolution — drop uploaded_doc fixture for v1):**
  1. **`TestHealthCheck`** — calls HealthCheck; asserts `version == "0.1.0-dev"`, `qdrant_ok` is True (Compose stack is up).
  2. **`TestSearchValidation`** — 4 tests:
     - `test_invalid_argument_on_empty_query`
     - `test_invalid_argument_on_bad_slug`
     - `test_invalid_argument_on_top_k_too_high`
     - `test_invalid_argument_when_only_active_false`
  3. **`TestSearchNotFound`** — search against a never-existed (tenant, bot) → NOT_FOUND.
  4. **`TestCrossTenantIsolation`** — search in tenant_b for a doc that exists in tenant_a (only as far as the failed lookup goes — both end in NOT_FOUND for this v1 test since the fixture doesn't actually upload). The behavior is: if (tenant_a, bot_a) has a collection but (tenant_b, bot_b) doesn't, search in tenant_b → NOT_FOUND. To make this meaningful, the test creates a Qdrant collection directly via `apps.qdrant_core.collection.create_collection_for_bot('test_t_a_*', 'test_b_a_*')`, then searches in `(test_t_b_*, test_b_b_*)`. Cleanup drops the test collection.
- **Skip-not-fail:** session-scoped `grpc_channel` fixture catches `grpc.FutureTimeoutError` and skips the suite if 50051 is unreachable. 5s timeout.
- **GRPC_HOST resolution (F4 resolution):** the fixture reads `os.environ.get('GRPC_HOST', 'localhost')` so the same test file works inside the web container (with `GRPC_HOST=grpc`) and from the host (default localhost).
- **No uploaded_doc fixture in v1.** End-to-end search-with-real-data is exercised manually via `verify_setup.py --full` + grpcurl smoke. A future Phase 7 addendum or Phase 8 ship gate may add a host-side upload helper.
- **Verification:**
  ```
  docker compose -f docker-compose.yml exec -e GRPC_HOST=grpc web pytest tests/test_search_grpc.py -v
  ```
  Or from host (skip-graceful):
  ```
  uv run pytest tests/test_search_grpc.py -v
  ```
- **Rollback:** `rm tests/test_search_grpc.py`.
- **Estimated effort:** 25 min.

### Step 3.14 — Docker stack rebuild

- **Goal:** Image with stubs baked in; grpc service running.
- **Commands:**
  ```
  make down
  make up
  sleep 90
  make ps                     # all healthy/running INCLUDING grpc
  make health                 # 200 OK from web /healthz; F19: also assert version 0.1.0-dev
  make health | grep "0.1.0-dev"
  ```
- **Diagnostics on failure:** `docker compose logs grpc --tail 100`. Common causes: import error (django.setup ordering), missing stubs (compile_proto.sh didn't run), bind error, port conflict.
- **Rollback:** `git diff --stat` (if git initialized; otherwise mtime audit) → revert Dockerfile + docker-compose.yml.
- **Estimated effort:** 10 min including the 90s wait.

### Step 3.15 — Manual `grpcurl` smoke (criterion 6)

- **Goal:** Exercise the four error paths + HealthCheck without the test runner.
- **Commands:** see §9 cheat-sheet.
- **Expected outputs:** INVALID_ARGUMENT for empty query, bad slug, only_active=false; NOT_FOUND for never-existed tenant; HealthCheck returns versioned response.
- **Fallback (no grpcurl installed):** use Python via `verify_setup.py --full`. Both prove the same wire path.
- **Estimated effort:** 5 min if grpcurl is on the host.

### Step 3.16 — Phase 1-6 regression

- **Commands:**
  ```
  uv run pytest -v                                         # host suite, ALL phases
  uv run ruff check . && uv run ruff format --check .
  make health                                              # Phase 1
  ```
- **Out-of-scope guard (F6 + general):**
  ```
  # If git initialized:
  git status --short
  # If not (per Phase 3 report's mtime audit pattern):
  find pyproject.toml uv.lock apps/core/ apps/tenants/ apps/documents/ \
       apps/ingestion/{embedder,chunker,payload,pipeline,locks}.py \
       apps/qdrant_core/{client,collection,exceptions,naming}.py \
       config/ tests/test_{healthz,models,naming,qdrant_client,qdrant_collection,chunker,payload,embedder,upload,locks,delete}.py \
       -newer build_prompts/phase_6_delete_api/implementation_report.md \
       2>/dev/null
  ```
  Should return EMPTY (no Phase 1-6 file mtime newer than Phase 6's report).
- **Expected:** 118+ tests still green (Phase 6 baseline). Phase 7 adds at least 12 unit + 7 integration = ~19 new tests.
- **Estimated effort:** 5-10 min depending on whether tests need a live Qdrant.

### Step 3.17 — Write `implementation_report.md` (Prompt 3 task)

- **Goal:** Final report per spec §"When you finish".
- Out-of-scope for THIS plan; Prompt 3 generates it.

---

## 4. Risk register

### R1 [critical] — qdrant-client query_points() API drift

The spec sketch's flat `prefetch=[dense_pf, sparse_pf]` shape doesn't perform fusion. The locked algorithm requires explicit RRF fusion before the ColBERT rerank. Verified at §6: qdrant-client 1.17.1 supports nested `Prefetch.prefetch` with `query=FusionQuery(fusion=Fusion.RRF)`. Plan resolves by nesting; no two-call fallback needed.

**Mitigation:** the call shape in step 3.6 is the verified-working shape; tests in step 3.12 assert it.

### R2 [critical] — Weighted RRF unavailable in qdrant-client 1.17.1

`Fusion` enum has only `RRF` and `DBSF` — no Weighted variant. `FusionQuery` has only the `fusion` field — no weight params. `Prefetch` has no `score_boost` or `weight` field. The locked spec requires 3:1 dense:sparse weighting.

**Mitigation:** duplicate the dense Prefetch 3 times in the inner prefetches list. Plain RRF over `[D, D, D, S]` produces `3 × 1/(k + r_d) + 1 × 1/(k + r_s)` per candidate IF Qdrant treats each input Prefetch as a separate ranked list (regardless of identity).

**Open verification (F1 [critical]):** during implementation, run a 30-second sanity check (see step 3.6) to confirm the duplication trick has effect on the installed Qdrant. If it doesn't, fall back to plain RRF with `[D, S]` and document the v2-deferred deviation in the implementation report.

### R3 [major] — Fusion stage limit budget

The spec says 50 dense + 50 sparse → fusion → rerank. With 3× duplication, the fusion stage sees 4 input Prefetches, each returning up to 50 candidates. Unique candidate count is ≤ 100 (50 dense + 50 sparse, possibly overlapping). The outer Prefetch's `limit=` should be the candidate count after fusion to feed into rerank. Choose `FUSION_LIMIT = 100` to give ColBERT a comfortable rerank set.

**Mitigation:** `FUSION_LIMIT = 100` constant; document in code that it's the per-fusion candidate budget, not per-prefetch.

### R4 [major] — `is_active=true` filter scope

The locked algorithm requires the filter to apply BOTH inside every leaf prefetch (so the prefetch returns only active candidates) AND on the final query (so a v2 atomic-version-swap pre-flip chunk that somehow survived can't leak in). Easy to miss one location.

**Mitigation:** build the filter once (`qfilter`) and pass it to every Prefetch's `filter=` arg AND to query_points's `query_filter=` arg. Unit test asserts `qfilter` appears in every leaf Prefetch.

### R5 [major] — Generated stub import path (sed fix)

protoc emits `import search_pb2` (non-relative). The compile_proto.sh sed step rewrites to `from . import search_pb2`. On Linux (compose containers, this dev box) GNU sed handles the inline edit. On macOS BSD sed, `-i` requires an empty argument: `sed -i '' '...'`. The fix only needs to work where the script runs (Linux during `docker build` and on this Linux dev host). If a developer ever runs this on macOS, they'll see the import fail — acceptable v1 limitation.

**Mitigation:** verify the import works after step 3.4. If ImportError, manually fix and document.

### R6 [major] — `django.setup()` ordering in server.py

`apps.qdrant_core.search` imports `apps.tenants.validators`, which imports `django.core.validators.RegexValidator`, which transitively touches `django.conf.settings`. Without `django.setup()` first, this raises `AppRegistryNotReady` at server startup. The grpc container would restart-loop indefinitely.

**Mitigation:** server.py first imports stdlib + django + grpc; THEN `django.setup()`; THEN any `apps.*` import (with `# noqa: E402` to silence ruff). Verification step 3.8 catches this.

### R7 [minor] — gRPC ThreadPoolExecutor max_workers

Default 10 workers each share the BGE-M3 process singleton. The first thread to call `embed_query` loads the model (~30s); subsequent threads share. For v1, default 10 is fine — torch+CPU ML kernels hold the GIL during inference, so over-provisioning workers doesn't help.

**Mitigation:** accept the default. `GRPC_MAX_WORKERS` env var is exposed for ops to tune later.

### R8 [minor] — gRPC server graceful shutdown

`server.stop(grace=10)` waits up to 10s for in-flight RPCs to finish before closing. Without a SIGTERM handler, `docker compose down` SIGKILLs the process, dropping mid-RPC requests. Plan adds both SIGTERM + SIGINT handlers.

### R9 [minor] — Cold-load latency tax on first Search

First Search RPC after stack startup pays ~30s for BGE-M3 to load in the grpc container's process. The integration test fixture's 5s `channel_ready_future` timeout doesn't cover model load latency — it only confirms TCP readiness. Tests issuing Search immediately after `make up` may time out.

**Mitigation:** integration tests call `HealthCheck` first (warm path; sub-millisecond), then a Search with a long timeout (10s+). The verify_setup `--full` flow runs warmup_embedder() in the WEB container, which does NOT warm the GRPC container's separate process. So the first Search RPC always pays the cold-load tax. Document in §6.

### R10 [minor] — Two BGE-M3 instances (8 GB box budget)

Each container that imports `apps.ingestion.embedder` and calls `_get_model()` loads its own ~1.8 GB fp16 model copy. After Phase 7 we have web + grpc both potentially loading. Concurrent peak: ~3.6 GB just for embedders, plus Qdrant's HNSW indexes, Postgres, Redis. Within an 8 GB box budget but tight.

**Mitigation:** v1 acceptable; flag as a Phase 8 concern.

### R11 [minor] — Restart policy preserved (F5)

Spec doesn't change `restart: unless-stopped`. If grpc fails on startup (import error, port conflict), Compose restarts it indefinitely. After 5 restarts in 60s, `docker compose ps` shows `restarting (1) X seconds ago`.

**Mitigation:** verify the policy is preserved in step 3.10's diff. If grpc keeps restarting, `docker compose logs grpc --tail 100` shows the import error.

### R12 [minor] — Fixture pollution

Each integration test should create a fresh (tenant, bot) and clean up. Same pattern as Phase 5/6. Plan reuses the established `fresh_bot` fixture pattern.

**Mitigation:** the `fresh_bot` fixture wraps `drop_collection(...)` in try/except (or `contextlib.suppress(Exception)` per Phase 3 deviation 1). Phase 6's `upload_lock` autouse patch is scoped to test_upload.py / test_locks.py; not relevant for grpc tests.

### R13 [minor] — Phase 1-6 regression — image cache invalidation

Adding `RUN bash scripts/compile_proto.sh` to the Dockerfile invalidates downstream layers. First rebuild after the change does a full reinstall of deps (~5-10 min). Subsequent builds reuse the dep layer if pyproject.toml is unchanged.

**Mitigation:** acceptable cost paid once. Document in step 3.9.

### R14 [minor] — `add_insecure_port` silent bind failure (F13)

`server.add_insecure_port("0.0.0.0:50051")` returns 0 on bind failure (port in use, permission denied). Without a return-value check, the server starts but binds to nothing — silent failure.

**Mitigation:** server.py checks return value and raises SystemExit if zero. Logs the bound port.

### R15 [minor] — Reflection unused (F3)

handler.py + server.py must NOT import `grpc_reflection.v1alpha.reflection`. v1 forbids reflection.

**Mitigation:** post-write `grep -r "reflection" apps/grpc_service/` returns empty.

### R16 [minor] — proto field number stability (F23)

Once `proto/search.proto` ships, removing or renumbering fields is a breaking change for clients with cached generated stubs. Documented in proto/search.proto's leading comment.

---

## 5. Verification checkpoints

| # | Checkpoint | Command | Expected |
|---|---|---|---|
| 5.1 | grpc_tools available | `uv run python -c "import grpc_tools; print('ok')"` | prints "ok"; exit 0 |
| 5.2 | compile_proto.sh exits 0 | `chmod +x scripts/compile_proto.sh && bash scripts/compile_proto.sh` | exits 0; "[compile_proto] Stubs generated" |
| 5.3 | Generated stubs importable | `uv run python -c "from apps.grpc_service.generated import search_pb2, search_pb2_grpc; print('ok')"` | prints "ok"; exit 0 |
| 5.4 | qdrant-client API verified | (already done — see §6) | findings recorded |
| 5.5 | search.py imports cleanly | `uv run python -c "from apps.qdrant_core.search import search, CollectionNotFoundError, DEFAULT_TOP_K, MAX_TOP_K, SCORE_THRESHOLD, PREFETCH_LIMIT, FUSION_LIMIT, RRF_DENSE_WEIGHT, RRF_SPARSE_WEIGHT; print('ok')"` | prints "ok"; exit 0 |
| 5.6 | manage.py check after search.py | `uv run python manage.py check` | exit 0 |
| 5.7 | handler.py imports cleanly | (django.setup first; see §3.7) | prints "ok, version= 0.1.0-dev"; exit 0 |
| 5.8 | server.py importable | (django.setup first; see §3.8) | prints "ok, port= 50051 workers= 10 grace= 10"; exit 0 |
| 5.8b | No reflection imports | `grep -r "reflection" apps/grpc_service/` | empty output |
| 5.9 | Dockerfile builds | `docker compose -f docker-compose.yml build` | builds the image; no errors |
| 5.10 | Compose config shows new grpc command | `docker compose -f docker-compose.yml config \| sed -n '/^  grpc:/,/^  [a-z]/p'` | shows `command: [sh, -c, ...]` with `python -m apps.grpc_service.server`; no `sleep infinity`; `restart: unless-stopped` preserved |
| 5.11 | Stack rebuild | `make down && make up && sleep 90 && make ps` | grpc container Status=`Up X seconds`, NOT `Created` |
| 5.12 | health check + version | `make health \| grep "0.1.0-dev"` | match found; exit 0 |
| 5.13 | verify_setup.py --help | `uv run python scripts/verify_setup.py --help` | argparse output |
| 5.13b | verify_setup stub-import smoke (F18) | `uv run python -c "from apps.grpc_service.generated import search_pb2_grpc; print('ok')"` | prints "ok" |
| 5.14 | verify_setup.py --full inside web | `docker compose -f docker-compose.yml exec -e GRPC_HOST=grpc web python scripts/verify_setup.py --full` | exits 0; HealthCheck + Search RPCs succeed |
| 5.15 | grpcurl smoke (4 paths + HealthCheck) | see §9 | INVALID_ARGUMENT × 3 + NOT_FOUND + HealthCheck OK |
| 5.16 | Unit tests | `uv run pytest tests/test_search_query.py -v` | green; ~5s; ≥12 tests |
| 5.17 | Integration tests inside web | `docker compose -f docker-compose.yml exec -e GRPC_HOST=grpc web pytest tests/test_search_grpc.py -v` | green; ≥7 tests |
| 5.17b | Integration tests from host (skip-graceful) | `uv run pytest tests/test_search_grpc.py -v` | passes if 50051 reachable, else skip |
| 5.18 | Phase 1-6 regression | `uv run pytest -v` | 118+ pre-existing tests still green; new ones added |
| 5.19 | Lint + format | `uv run ruff check . && uv run ruff format --check .` | no violations |
| 5.20 | Out-of-scope guard | mtime audit (per §3.16) OR `git status --short` | only the 11 expected files modified/created |

---

## 6. Spec ambiguities & open questions (and qdrant-client API verification findings)

### qdrant-client API verification (run BEFORE writing search.py)

Inspection script output (qdrant-client 1.17.1):

```
=== qdrant_client version ===
1.17.1

=== query_points signature ===
(self, collection_name: str,
 query: Union[int, str, UUID, PointId, list[float], list[list[float]],
              SparseVector, NearestQuery, RecommendQuery, DiscoverQuery,
              ContextQuery, OrderByQuery, FusionQuery, RrfQuery, FormulaQuery,
              SampleQuery, RelevanceFeedbackQuery, ndarray, Document, Image,
              InferenceObject, NoneType] = None,
 using: str | None = None,
 prefetch: Prefetch | list[Prefetch] | None = None,
 query_filter: Filter | None = None,
 search_params: SearchParams | None = None,
 limit: int = 10,
 offset: int | None = None,
 with_payload: ... = True,
 with_vectors: ... = False,
 score_threshold: float | None = None,
 lookup_from: ... = None,
 consistency: ... = None,
 shard_key_selector: ... = None,
 timeout: int | None = None,
 **kwargs)

=== Prefetch class fields ===
Prefetch fields: ['prefetch', 'query', 'using', 'filter', 'params',
                  'score_threshold', 'limit', 'lookup_from']

=== Fusion enum ===
[<Fusion.RRF: 'rrf'>, <Fusion.DBSF: 'dbsf'>]

=== FusionQuery ===
FusionQuery fields: ['fusion']

=== MatchAny ===
MatchAny fields: ['any']

=== Available models with 'fusion' ===
['Fusion', 'FusionQuery']

=== Available models with 'weight' ===
[]

=== Available models with 'rerank' ===
[]
```

### Findings & adaptations

1. **`query_points()` supports the multi-stage shape natively.** No two-call fallback needed.

2. **`Prefetch` has a `prefetch` field (recursive nesting).** This is the proper way to chain stages: an outer Prefetch can contain inner Prefetches, fusing them with `query=FusionQuery(...)`. Final query operates on the outer Prefetch's output.

3. **No Weighted RRF.** Only `Fusion.RRF` and `Fusion.DBSF`. `FusionQuery` only takes `fusion=`. `Prefetch` has no `score_boost`/`weight`. **Resolution: duplicate the dense prefetch 3× in the nested prefetches list to emulate 3:1 weighting via plain RRF input frequency.** See R2.

4. **`MatchAny(any=[...])`**. Confirmed kwarg name. Used for repeated string filters (source_types, tags).

5. **Final adapted call shape:** see §3.6.

### Other ambiguities (spec-side)

A1. **HealthCheck reads `_get_model.cache_info().currsize`** — Phase 4's private function with a leading underscore. Acceptable for v1 (Phase 4 spec doesn't forbid). Document the cross-module dependency in implementation report.

A2. **`Chunk.tenant_id` assertion in spec test code.** The proto's `Chunk` message has no `tenant_id` field — the spec test line `assert chunk.tenant_id == ""` won't compile. Plan removes that line from `test_search_returns_relevant_chunks` (and the test itself is dropped per F4).

A3. **`uploaded_doc` fixture cross-process problem.** Resolved in F4: drop the fixture for v1; live-data search testing is manual via verify_setup.py --full + grpcurl. Phase 7 ships with validation/NOT_FOUND/HealthCheck/cross-tenant isolation tests only.

A4. **`only_active=true` proto3 default.** Per spec pitfall #9, proto3 bool defaults to false; clients must set `only_active=true` explicitly. Handler returns INVALID_ARGUMENT on false. Spec is unambiguous; no plan deviation.

A5. **`Chunk.page_number=0` ambiguity.** Per spec pitfall #10, proto3 int32 default is 0; can't distinguish "page 0" from "absent". v1 convention: 0=absent.

A6. **`@with_retry()` decorator on `_execute_query`.** Phase 3's retry catches transient gRPC errors via `_is_transient`. `query_points` may raise different exception classes than create/upsert/delete. Plan accepts the existing decorator; non-retryable errors propagate as-is and the handler catches via `QdrantError` / `Exception`.

A7. **`verify_setup.py --full` GRPC_HOST.** Inside the web container, `localhost:50051` doesn't resolve to the grpc service — only the web container's own loopback. The grpc service is at `grpc:50051` on the docker network. Plan adds `GRPC_HOST = os.environ.get('GRPC_HOST', 'localhost')` so callers can override. Documented in step 3.11. Operator running from inside web: `docker compose exec -e GRPC_HOST=grpc web python scripts/verify_setup.py --full`.

A8. **gRPC reflection.** Spec says no reflection in v1; grpcurl users must `-import-path proto/ -proto search.proto`. Verified via grep in step 3.8.

A9. **Server logs `query_length` not `query`.** Plan keeps the spec's logging shape — no PII (the query string) in logs.

A10. **Slow-query log threshold.** Spec doesn't set one. Plan doesn't add one (v1 simplicity); Phase 8 owns it.

A11 [F2 minor]. **`score_threshold=0.65` against unbounded ColBERT max_sim.** ColBERT max_sim scores are sum-over-tokens, unbounded. The spec's 0.65 is empirical; v1 honors it. Phase 8 metrics will expose whether it's too restrictive or too lax.

A12 [F4 partial]. **Cross-tenant isolation test.** Test creates a Qdrant collection directly via `apps.qdrant_core.collection.create_collection_for_bot()` for tenant_a, then searches in tenant_b — expects NOT_FOUND. The collection-creation side effect is necessary because the search NEVER auto-creates. Cleanup drops both collections.

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope — never touched

- `apps/core/views.py` (healthz)
- `apps/core/urls.py`
- `apps/tenants/{models,admin,validators,migrations/}.py`
- `apps/documents/{models,serializers,views,urls,migrations/}.py`
- `apps/ingestion/{embedder,chunker,payload,pipeline,locks}.py`
- `apps/qdrant_core/{client,collection,exceptions,naming}.py` (only ADD search.py — others unchanged)
- `apps/grpc_service/{__init__,apps}.py` (Phase 1 stubs, unchanged)
- `config/{settings,urls,wsgi,asgi,celery}.py`
- `manage.py`
- `pyproject.toml` — no new deps (grpcio + grpcio-tools already pinned)
- `uv.lock`
- `docker-compose.override.yml`
- `Makefile`
- `tests/test_healthz.py`, `tests/test_models.py`, `tests/test_naming.py`, `tests/test_qdrant_client.py`, `tests/test_qdrant_collection.py`, `tests/test_chunker.py`, `tests/test_payload.py`, `tests/test_embedder.py`, `tests/test_upload.py`, `tests/test_locks.py`, `tests/test_delete.py` (and conftest.py)
- `.env.example`, `.gitignore`, `.python-version`
- `rag_system_guide.md`, `README.md`

### Phase 7 explicit modifies (4)

- `Dockerfile` — add ONE `RUN bash scripts/compile_proto.sh` line in builder stage
- `docker-compose.yml` — replace grpc service `command:` only (keeping all 9 other fields)
- `scripts/compile_proto.sh` — fill in the protoc invocation
- `scripts/verify_setup.py` — add `_search_roundtrip()` + wire into `--full`

### Phase 7 new (7 + 1 generated package marker = 8)

- `proto/search.proto`
- `apps/grpc_service/generated/__init__.py` (empty)
- `apps/grpc_service/server.py`
- `apps/grpc_service/handler.py`
- `apps/qdrant_core/search.py`
- `tests/test_search_query.py`
- `tests/test_search_grpc.py`
- `build_prompts/phase_7_search_grpc/implementation_report.md` (Prompt 3 task)

### Generated (gitignored, not committed)

- `apps/grpc_service/generated/search_pb2.py`
- `apps/grpc_service/generated/search_pb2_grpc.py`

---

## 8. Acceptance-criteria mapping

| # | Criterion | Step | Verify | Expected |
|---|---|---|---|---|
| 1 | `bash scripts/compile_proto.sh` exits 0 + creates `search_pb2*.py` | 3.2, 3.4 | step 5.2, 5.3 | exit 0; both .py files importable |
| 2 | `ruff check .` zero violations | 3.6-3.13 (write clean) | step 5.19 | "All checks passed!" |
| 3 | `ruff format --check .` zero changes | 3.6-3.13 | step 5.19 | "X files already formatted" |
| 4 | `manage.py check` exit 0 | 3.6, 3.7 | step 5.6 | "System check identified no issues (0 silenced)." |
| 5 | Stack rebuild + make health green; grpc running | 3.9, 3.10, 3.14 | step 5.11, 5.12 | grpc container Up; healthz returns green JSON with version 0.1.0-dev |
| 6 | grpcurl empty-query → INVALID_ARGUMENT | 3.15 | step 5.15 | grpcurl output: ERROR: Code: InvalidArgument |
| 7 | verify_setup.py --full inside web exit 0 | 3.11, 3.14 | step 5.14 | "[verify_setup] All checks passed." |
| 8 | `pytest tests/test_search_query.py -v` green (mocked) | 3.12 | step 5.16 | green; <5s; ≥12 tests |
| 9 | `pytest tests/test_search_grpc.py -v` green inside container (real stack + warm embedder) | 3.13, 3.14 | step 5.17 | green; ≥7 tests |
| 10 | Phase 1+2+3+4+5a+5b+6 regression — full suite green; healthz still 200 | 3.16 | step 5.18, 5.12, 5.20 | 118+ pre-existing tests + new ones; healthz 200; no Phase 1-6 file modified |

---

## 9. Tooling commands cheat-sheet

```bash
# === Phase A: proto + compile ===
chmod +x scripts/compile_proto.sh
bash scripts/compile_proto.sh
ls apps/grpc_service/generated/
uv run python -c "from apps.grpc_service.generated import search_pb2, search_pb2_grpc; print('imports ok')"

# === Phase B: qdrant-client API verification (already done; cmd preserved) ===
uv run --no-sync python << 'EOF'
from inspect import signature
from qdrant_client import QdrantClient
from qdrant_client import models as m
from importlib.metadata import version
print(version("qdrant-client"))
print(signature(QdrantClient.query_points))
print(list(m.Prefetch.model_fields.keys()))
print(list(m.Fusion))
print(list(m.FusionQuery.model_fields.keys()))
print(list(m.MatchAny.model_fields.keys()))
EOF

# === Phase C-E: source files ===
uv run python -c "from apps.qdrant_core.search import search; print('ok')"
uv run python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()
from apps.grpc_service.handler import VectorSearchService, VERSION
from apps.grpc_service.server import serve, DEFAULT_PORT, DEFAULT_MAX_WORKERS
print('ok', VERSION, DEFAULT_PORT, DEFAULT_MAX_WORKERS)
"
grep -r "reflection" apps/grpc_service/   # F3: expect empty
uv run python manage.py check

# === Phase F-H: Docker ===
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml config | sed -n '/^  grpc:/,/^  [a-z]/p'
make down && make up && sleep 90 && make ps && make health
make health | grep "0.1.0-dev"           # F19
docker compose -f docker-compose.yml ps grpc
docker compose -f docker-compose.yml logs grpc --tail 50

# === Phase I: grpcurl smoke ===
# 1. Empty query → INVALID_ARGUMENT
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"t1","bot_id":"b1","query":"","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 | head

# 2. Bad slug → INVALID_ARGUMENT
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"Bad-Slug","bot_id":"b1","query":"x","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 | head

# 3. only_active=false → INVALID_ARGUMENT
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"t1","bot_id":"b1","query":"x","filters":{"only_active":false}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 | head

# 4. Valid request to non-existent bot → NOT_FOUND
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"never_existed","bot_id":"x123","query":"x","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 | head

# 5. HealthCheck → OK
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{}' localhost:50051 qdrant_rag.v1.VectorSearch/HealthCheck

# Fallback if no grpcurl on host:
docker compose -f docker-compose.yml exec -e GRPC_HOST=grpc web python scripts/verify_setup.py --full

# === Phase J: tests ===
uv run pytest tests/test_search_query.py -v
docker compose -f docker-compose.yml exec -e GRPC_HOST=grpc web pytest tests/test_search_grpc.py -v
uv run pytest tests/test_search_grpc.py -v   # host (skip-not-fail if 50051 unreachable)

# === Phase K: full suite + regression + lint ===
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
make health    # Phase 1 still green

# Out-of-scope guard (per Phase 3 mtime-audit pattern; project is not under git per Phase 3 report)
find pyproject.toml uv.lock apps/core/ apps/tenants/ apps/documents/ \
     apps/ingestion/embedder.py apps/ingestion/chunker.py apps/ingestion/payload.py \
     apps/ingestion/pipeline.py apps/ingestion/locks.py \
     apps/qdrant_core/client.py apps/qdrant_core/collection.py \
     apps/qdrant_core/exceptions.py apps/qdrant_core/naming.py \
     config/ \
     -newer build_prompts/phase_6_delete_api/implementation_report.md \
     2>/dev/null   # expect empty
```

---

## 10. Estimated effort

| Step | Estimate |
|---|---|
| 3.1 search.proto | 5 min |
| 3.2 compile_proto.sh | 5 min |
| 3.3 generated/__init__.py | 1 min |
| 3.4 first stub generation + verify | 2 min |
| 3.5 API inspection (DONE) | 0 |
| 3.6 search.py (with shape adaptation + weighting verification) | 45 min |
| 3.7 handler.py | 25 min |
| 3.8 server.py + reflection grep | 15 min |
| 3.9 Dockerfile + first rebuild | 15 min (rebuild dominates) |
| 3.10 docker-compose.yml | 5 min |
| 3.11 verify_setup.py extension | 15 min |
| 3.12 test_search_query.py (more tests than spec) | 30 min |
| 3.13 test_search_grpc.py (no uploaded_doc) | 25 min |
| 3.14 stack up + smoke + version assertion | 10 min |
| 3.15 grpcurl 4-path | 5 min |
| 3.16 regression + mtime audit | 10 min |
| 3.17 implementation_report.md (Prompt 3) | 30 min |
| **Total** | **~3.7 hours** |

The longest non-build steps remain the API-shape adaptation + weighting verification (R1/R2/F1) and the test plan revisions. The plan resolves all 2 critical and 6 major findings.

---

## End of plan
