# Phase 7 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **BUILD per the revised plan, VERIFY against the spec, REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_7_search_grpc/spec.md` — re-read in full.
2. `build_prompts/phase_7_search_grpc/plan.md` — revised plan from Step 2.
3. `build_prompts/phase_7_search_grpc/plan_review.md` — critique. Don't re-litigate.
4. `build_prompts/phase_4_embedding_chunking/spec.md` + report — Phase 4 contract.
5. `build_prompts/phase_3_qdrant_layer/spec.md` + report — Phase 3 contract.
6. `build_prompts/phase_5b_upload_idempotency/spec.md` + report — write-path contract.
7. `build_prompts/phase_6_delete_api/spec.md` + report — delete contract.
8. `build_prompts/phase_2_domain_models/spec.md` + report — Phase 2 contract.
9. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.

If any required input is missing, abort.

---

## Hard rules

1. Follow the revised plan. Document deviations.
2. Build in plan order.
3. Run verification at every checkpoint.
4. Honor "Out of scope" — no reflection, no streaming, no auth, no metrics, no MMR, no caching.
5. Modify ONLY: `Dockerfile`, `docker-compose.yml`, `scripts/{compile_proto.sh, verify_setup.py}`. Add: everything in `proto/`, `apps/grpc_service/{generated,server,handler}.py`, `apps/qdrant_core/search.py`, `tests/test_search_*.py`. NO modification to ANY other Phase 1-6 file.
6. No code comments unless spec/invariant justifies.
7. Never commit `.env` or generated stubs (`apps/grpc_service/generated/{search_pb2,search_pb2_grpc}.py` are gitignored).
8. **Verify the qdrant-client `query_points()` API signature BEFORE writing search.py.** Use `inspect.signature` (and check `Prefetch.__init__`, `Fusion`, `MatchAny`). Document findings in implementation report.
9. **Verify `django.setup()` is called BEFORE any `apps.*` import in server.py.**
10. **Verify the generated `search_pb2_grpc.py`'s import is package-relative** after `compile_proto.sh` runs (the sed step). If sed didn't fix it, manually fix and document.
11. No emoji. No `*.md` beyond `implementation_report.md`.

---

## Implementation phases

### Phase A — proto + compile_proto.sh

Write `proto/search.proto` per spec. Fill in `scripts/compile_proto.sh` per spec.

**Verify locally:**
```bash
chmod +x scripts/compile_proto.sh
bash scripts/compile_proto.sh
ls apps/grpc_service/generated/
```
Expected: `__init__.py`, `search_pb2.py`, `search_pb2_grpc.py`.

```bash
uv run python -c "from apps.grpc_service.generated import search_pb2, search_pb2_grpc; print('imports ok')"
```

If the import fails, the sed step in compile_proto.sh didn't apply. Manually fix:
```bash
sed -i 's/^import search_pb2/from . import search_pb2/' apps/grpc_service/generated/search_pb2_grpc.py
```

### Phase B — qdrant-client API verification

Run BEFORE writing search.py:

```bash
uv run python << 'EOF'
from inspect import signature
from qdrant_client import QdrantClient
from qdrant_client import models as m

print("=== query_points signature ===")
print(signature(QdrantClient.query_points))

print("\n=== Prefetch class ===")
print(signature(m.Prefetch.__init__))

print("\n=== Fusion enum ===")
print(list(m.Fusion))

if hasattr(m, "FusionQuery"):
    print("\n=== FusionQuery ===")
    print(signature(m.FusionQuery.__init__))

print("\n=== MatchAny ===")
print(signature(m.MatchAny.__init__))

print("\n=== Available models with 'fusion' ===")
print([n for n in dir(m) if 'usion' in n.lower()])
print("\n=== Available models with 'weight' ===")
print([n for n in dir(m) if 'eight' in n.lower()])
EOF
```

Document the output in your final implementation report. Adapt search.py's `_execute_query` shape if the API differs from the spec sketch. The SEMANTICS are locked:
- 50 dense + 50 sparse prefetch
- Weighted RRF fusion (3:1 dense:sparse)
- ColBERT rerank on the fused candidates
- 0.65 score threshold
- top_k limit
- `is_active=true` filter inside both prefetches AND the final query

### Phase C — search.py (qdrant_core)

Write `apps/qdrant_core/search.py` per spec, adapting for Phase B's findings.

**Verify (no real Qdrant):**
```bash
uv run python -c "
from apps.qdrant_core.search import search, CollectionNotFoundError
print('imports ok')
"
uv run python manage.py check
```

### Phase D — handler.py

Write `apps/grpc_service/handler.py` per spec.

**Verify:**
```bash
uv run python -c "
from apps.grpc_service.handler import VectorSearchService, VERSION
print('imports ok, version=', VERSION)
"
```

### Phase E — server.py

Write `apps/grpc_service/server.py` per spec. CRITICAL: `django.setup()` must come BEFORE any `apps.*` import.

**Verify (don't actually start it yet):**
```bash
uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()
from apps.grpc_service.server import serve
print('imports ok')
"
```

### Phase F — Dockerfile + docker-compose.yml

Modify `Dockerfile`: add `RUN bash scripts/compile_proto.sh` AFTER `COPY . .`. Verify the line position is correct (after source is copied, before runtime).

Modify `docker-compose.yml`: replace the grpc service's `command:` with the YAML list-form pattern from spec (avoiding the folded-scalar trap from Phase 1).

**Verify:**
```bash
docker compose -f docker-compose.yml config | sed -n '/^  grpc:/,/^  [a-z]/p'
```
Confirm the command is `python -m apps.grpc_service.server` and is in YAML list form.

### Phase G — verify_setup.py extension

Extend `scripts/verify_setup.py` per spec. Phase 1's default behavior + Phase 3's collection round-trip + Phase 4's embedder warmup are all preserved.

The Search RPC round-trip uses `os.environ.get("GRPC_HOST", "localhost")` so it works from both host and inside the web container (where it'd need `GRPC_HOST=grpc`).

**Verify (no full run yet):**
```bash
uv run python scripts/verify_setup.py --help    # parses args
```

### Phase H — Stack rebuild

```bash
make down
make up
sleep 90
make ps                                          # all healthy/running INCLUDING grpc
```

The grpc container should now be running (not `Created`). Confirm:
```bash
docker compose -f docker-compose.yml ps grpc
```
Status should show `Up X seconds`.

If grpc container exits immediately, check logs:
```bash
docker compose -f docker-compose.yml logs grpc --tail 50
```
Common causes: import error (django.setup ordering), missing generated stubs (compile_proto.sh didn't run in Dockerfile), bind error (port already in use).

### Phase I — Manual grpcurl smoke

If `grpcurl` is installed on the host:

```bash
# 1. Empty query → INVALID_ARGUMENT
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"t1","bot_id":"b1","query":"","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 || true

# 2. Bad slug → INVALID_ARGUMENT
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"Bad-Slug","bot_id":"b1","query":"x","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 || true

# 3. only_active=false → INVALID_ARGUMENT
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"t1","bot_id":"b1","query":"x","filters":{"only_active":false}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 || true

# 4. Valid request to non-existent bot → NOT_FOUND
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{"tenant_id":"never_existed","bot_id":"x123","query":"x","filters":{"only_active":true}}' \
    localhost:50051 qdrant_rag.v1.VectorSearch/Search 2>&1 || true

# 5. HealthCheck → OK
grpcurl -plaintext -import-path proto/ -proto search.proto \
    -d '{}' localhost:50051 qdrant_rag.v1.VectorSearch/HealthCheck
```

If `grpcurl` is NOT installed, use Python equivalents:
```bash
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
```
This issues the same RPCs.

### Phase J — Tests

```bash
# Unit tests (no real stack needed)
uv run pytest tests/test_search_query.py -v

# Integration tests (real stack with embedder warm)
docker compose -f docker-compose.yml exec web pytest tests/test_search_grpc.py -v
# OR from host (skip-not-fail if 50051 unreachable):
uv run pytest tests/test_search_grpc.py -v
```

### Phase K — Full suite + regression

```bash
docker compose -f docker-compose.yml exec web pytest -v        # all tests in container
uv run pytest -v                                                # host (embedder-loading skips ok)
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run

# Phase 1-6 regression
make health                                                     # Phase 1
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py tests/test_delete.py -v   # Phase 5+6

git status --short
```

Files in `git status --short` must be:
- `proto/search.proto` (new)
- `apps/grpc_service/generated/__init__.py` (new — empty marker)
- `apps/grpc_service/server.py` (new)
- `apps/grpc_service/handler.py` (new)
- `apps/qdrant_core/search.py` (new)
- `Dockerfile` (modified)
- `docker-compose.yml` (modified)
- `scripts/compile_proto.sh` (filled in)
- `scripts/verify_setup.py` (extended)
- `tests/test_search_query.py` (new)
- `tests/test_search_grpc.py` (new)
- `build_prompts/phase_7_search_grpc/implementation_report.md` (new)

NOT in git (gitignored):
- `apps/grpc_service/generated/search_pb2.py`
- `apps/grpc_service/generated/search_pb2_grpc.py`

Anything else in the diff is a deviation requiring justification.

---

## Self-review

After Phase K passes, run self-review against the **spec**.

For each acceptance criterion (10): pass/fail, command run, output, notes.
For each pitfall (10): avoided/hit/N/A, how confirmed.
For each "Out of scope" item: confirmed not implemented.

---

## Final report

Save to `build_prompts/phase_7_search_grpc/implementation_report.md`. Standard structure plus:

- **qdrant-client `query_points()` API verification** — paste the inspection output. Note any deviations from the spec sketch and how `_execute_query` adapted.
- **`compile_proto.sh` sed fix** — confirm the import in `search_pb2_grpc.py` was patched correctly, OR document if it had to be patched manually.
- **gRPC container startup** — confirm the container is running (not Created) after `make up`.
- **Phase 1+2+3+4+5a+5b+6 regression** — paste `git diff --name-only` to prove no prior-phase file was modified outside the 4 explicitly-modified ones.

---

## What "done" looks like

Output to chat:

1. Path to `implementation_report.md`.
2. Overall status: PASS / FAIL / PARTIAL.
3. Acceptance criteria score: X/10.
4. qdrant-client `query_points()` API deviations (one line).
5. Phase 1-6 regression: PASS / FAIL.
6. Recommended next step (Phase 8 unblocked? — final phase).

Then **stop**.

---

## A note on honesty

If the qdrant-client API required a two-call fallback (prefetch+fuse, then rerank), document it clearly. If a test flaked due to BGE-M3 cold-load, say so. If the gRPC container restart-loops because of an import error, paste the logs. The report is the contract — write it to be true, not flattering.
