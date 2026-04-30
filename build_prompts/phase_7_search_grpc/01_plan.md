# Phase 7 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not run `compile_proto.sh`. Do not modify any file.**

---

## Required reading (in this order)

1. `README.md` — project charter.
2. `build_prompts/phase_7_search_grpc/spec.md` — full Phase 7 spec. **Source of truth. Read twice.**
3. `build_prompts/phase_4_embedding_chunking/spec.md` — `embed_query()`, `sparse_to_qdrant()`, `colbert_to_qdrant()`.
4. `build_prompts/phase_3_qdrant_layer/spec.md` — `get_qdrant_client()`, `with_retry`, vector schema names.
5. `build_prompts/phase_3_qdrant_layer/implementation_report.md` — qdrant-client API surface confirmed for create/delete; Phase 7 verifies `query_points` fresh.
6. `build_prompts/phase_2_domain_models/spec.md` — `slug_validator`, `validate_slug`, `collection_name()`.
7. `build_prompts/phase_1_foundation/spec.md` — Docker Compose layout; pitfall #14a (YAML folded scalar gotcha for compose `command:`).
8. `rag_system_guide.md` if present — §3 "Part B — Query Flow."

If `phase_7_search_grpc/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_7_search_grpc/plan.md
```

---

## What the plan must contain

### 1. Plan summary

3–5 sentences. What's getting built? What's the riskiest part? How will the build verify itself?

### 2. Build order & dependency graph

Phase 7 has more files than any prior phase. Dependencies:

- `proto/search.proto` first (no code deps).
- `compile_proto.sh` next — generates the stubs into `apps/grpc_service/generated/`. Run it once locally to inspect output.
- `apps/grpc_service/generated/__init__.py` — empty package marker.
- **API verification step** — inspect `client.query_points` signature BEFORE writing `search.py`.
- `apps/qdrant_core/search.py` — uses `embed_query`, `get_qdrant_client`, `collection_name`, `with_retry`. Adapts to actual qdrant-client API.
- `apps/grpc_service/handler.py` — uses generated stubs + search.py + slug_validator.
- `apps/grpc_service/server.py` — uses handler + Django setup.
- `Dockerfile` modification — add `RUN bash scripts/compile_proto.sh`.
- `docker-compose.yml` modification — replace grpc command.
- `scripts/verify_setup.py` extension — add Search RPC round-trip in `--full`.
- `tests/test_search_query.py` — unit (no Qdrant or embedder).
- `tests/test_search_grpc.py` — integration (real stack).

### 3. Build steps (sequenced)

12–16 numbered steps. Each: goal · files · verification · rollback.

Critical sequencing:
- Generate stubs LOCALLY (via compile_proto.sh) to inspect what classes/functions are produced. The agent then knows the stub structure when writing handler.py.
- Run the qdrant-client API inspection BEFORE writing search.py. Document the actual `query_points()` signature in plan section 6.
- Stack rebuild AFTER all source files exist + Dockerfile + docker-compose.yml updated.
- Manual `grpcurl` smoke BEFORE running pytest.
- Phase 1-6 regression LAST.

### 4. Risk register

Cover at minimum:

- **qdrant-client `query_points()` API drift.** The locked algorithm requires prefetch + fusion + multivector rerank in one call. Some versions don't support all stages in one call. Plan must include the fallback (two-call pattern) and document which version's API was found.
- **Fusion weight location.** `Fusion.RRF` vs `WeightedFusion` vs `Prefetch.score_boost`. Verify with `inspect`.
- **`Prefetch` parameter names.** `using=` vs `vector_name=`; `query=` vs `vector=`. Verify.
- **`MatchAny` for `repeated string` filters.** Confirm the class name and shape.
- **Generated stub import.** `search_pb2_grpc.py` imports `search_pb2` non-relatively. `compile_proto.sh` sed-fixes this. If sed fails (e.g., on macOS BSD sed vs GNU sed), the fix doesn't apply and the import breaks. Plan should test the sed fix on Linux (Compose runs Linux containers; this is fine).
- **`django.setup()` order in server.py.** Must come before importing any module that touches `django.conf.settings`. Otherwise `AppRegistryNotReady` at server startup.
- **gRPC ThreadPoolExecutor max_workers.** Default 10. Each thread can issue concurrent embeds → embedder is process-shared (single GPU/CPU lock). Plan should accept v1 default.
- **Server graceful shutdown.** SIGTERM handler in server.py uses `server.stop(grace=10)`. Without this, `docker compose down` kills mid-RPC requests. Verify the signal handler.
- **Cold-load latency tax on first Search.** ~30s. Phase 7 expects operators to run `verify_setup.py --full` post-deploy. Tests' first call also pays it; mark embedder-loading tests with `@pytest.mark.embedder`.
- **`grpc.insecure_channel("localhost:50051")` from test runner.** Tests run on the host shell; port 50051 must be exposed (it is, per Compose). Test fixture uses 5s timeout to skip-not-fail if server isn't reachable.
- **Phase 1–6 regression.** Dockerfile change invalidates the image cache, forcing a full rebuild. Image size grows ~1 MB for the stubs. Verify `make health` still works after rebuild.
- **Fixture pollution.** Each test creates a unique `(tenant_id, bot_id)` and drops the collection in teardown. Same pattern as Phase 5/6.

### 5. Verification checkpoints

10–14 with exact commands and expected outcomes:

- After search.proto: `python -c "import grpc_tools; print('grpc_tools available')"`.
- After compile_proto.sh: `bash scripts/compile_proto.sh` exits 0; `apps/grpc_service/generated/search_pb2.py` exists; `from apps.grpc_service.generated import search_pb2` imports cleanly.
- API verification step (BEFORE writing search.py): print `inspect.signature(client.query_points)`, `qdrant_client.models.Prefetch.__init__`, `qdrant_client.models.Fusion`, `qdrant_client.models.MatchAny`. Document findings in plan section 6.
- After search.py: import smoke; `manage.py check` exits 0.
- After handler.py: import smoke; `manage.py check`.
- After server.py: import smoke; can be invoked with `--help` if applicable; otherwise `python -c "from apps.grpc_service.server import serve; print('ok')"`.
- After Dockerfile modification: `docker compose build` succeeds.
- After docker-compose.yml modification: `docker compose config` shows the new grpc command in YAML list form.
- After stack rebuild + `make up`: `docker compose ps` shows grpc container running (no longer `Created` from Phase 1's `sleep infinity`).
- After verify_setup.py extension: `docker compose exec web python scripts/verify_setup.py --full` exits 0; logs show HealthCheck + Search RPCs succeeded.
- Manual grpcurl smoke: `grpcurl -plaintext -import-path proto/ -proto search.proto -d '...' localhost:50051 qdrant_rag.v1.VectorSearch/Search`. Test 4 paths: bad slug → INVALID_ARGUMENT; empty query → INVALID_ARGUMENT; only_active=false → INVALID_ARGUMENT; valid request to non-existent bot → NOT_FOUND.
- After tests: `uv run pytest tests/test_search_query.py -v` green (no real stack); `docker compose exec web pytest tests/test_search_grpc.py -v` green (real stack with embedder warm).
- Phase 1-6 regression: `uv run pytest -v` (host) keeps all 118+ tests green; `make health` 200.
- Out-of-scope guard: `git status --short` shows ONLY expected files.

### 6. Spec ambiguities & open questions (and the API verification findings)

5–10 entries. Things to scrutinize. CRITICAL: this section is where the API verification findings live.

After running:

```bash
uv run python -c "
from inspect import signature
from qdrant_client import QdrantClient
from qdrant_client.models import Prefetch, Fusion, FusionQuery, MatchAny, MatchValue, FieldCondition, Filter, NamedVector, NamedSparseVector, SparseVector
print('--- query_points signature ---')
print(signature(QdrantClient.query_points))
print('--- Prefetch ---')
print(Prefetch.__init__.__doc__ or 'no docstring')
print(signature(Prefetch.__init__))
print('--- Fusion ---')
print(list(Fusion))
print('--- FusionQuery ---')
print(getattr(FusionQuery, '__init__', None) and signature(FusionQuery.__init__))
print('--- MatchAny ---')
print(signature(MatchAny.__init__))
"
```

Document the actual signatures in the plan. If `query_points` doesn't accept the spec's combined-stage shape, the plan must propose the fallback two-call structure.

Other ambiguities:

- **gRPC server's `django.setup()` placement.** The spec puts it at the top of server.py. But server.py is invoked as `python -m apps.grpc_service.server` — Python imports apps/grpc_service/__init__.py first, which is the existing AppConfig stub. Does importing the AppConfig require Django setup? Verify.
- **`verify_setup.py --full`'s search round-trip uses `localhost:50051`** when run inside the web container. Inside Docker's network, the grpc service is at `grpc:50051` (service-name DNS). `localhost` works only because the web container has its own loopback — but the grpc server isn't on the web container's loopback! Plan must override: use `os.environ.get("GRPC_HOST", "localhost")` in verify_setup.py and document that running inside the web container requires `GRPC_HOST=grpc`.
- **`MatchAny(any=...)` for repeated string filters.** Verify the keyword argument name.
- **`Chunk.page_number = 0` for absent.** Proto3 default for int32 is 0. Document the convention.
- **gRPC server's HealthCheck checks `_get_model.cache_info().currsize > 0`** — accesses Phase 4's private function. Acceptable for v1; Phase 4's spec doesn't forbid it. Document the cross-module dependency.
- **`with_retry()` decorator on `_execute_query` in search.py.** The decorator catches transient gRPC errors. But `query_points` may not raise the exact exceptions Phase 3's retry decorator catches. Verify.

### 7. Files deliberately NOT created / NOT modified

Echo spec.md's "Out of scope" + the don't-touch list. Specifically: Phase 5/6's HTTP write path is locked; no changes to apps/documents/, apps/ingestion/, apps/tenants/, config/, apps/core/.

### 8. Acceptance-criteria mapping

For all 10 criteria: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Generate stubs
bash scripts/compile_proto.sh

# Verify generated stubs work
uv run python -c "from apps.grpc_service.generated import search_pb2, search_pb2_grpc; print('ok')"

# qdrant-client API verification
uv run python -c "from inspect import signature; from qdrant_client import QdrantClient; print(signature(QdrantClient.query_points))"

# Docker
make up && sleep 90 && make health
docker compose -f docker-compose.yml ps                                 # all healthy + grpc running
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full

# Manual grpcurl smoke
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"t1","bot_id":"b1","query":"","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search

# Tests
uv run pytest tests/test_search_query.py -v
docker compose -f docker-compose.yml exec web pytest tests/test_search_grpc.py -v
uv run pytest -v
uv run ruff check . && uv run ruff format --check .
```

### 10. Estimated effort

Per step. Phase 7 has more files than any prior phase but the algorithm is small once the API is understood.

---

## Output format

Single markdown file at `build_prompts/phase_7_search_grpc/plan.md`. 500–800 lines.

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Total line count.
3. 5-bullet summary (especially: how the qdrant-client API verification finding shapes the build).
4. Spec ambiguities flagged in section 6 (titles).

Then **stop**.
