# Phase 7 — Search (gRPC)

> **Audience:** A coding agent building on top of verified-green Phases 1–6 at `/home/bol7/Documents/BOL7/Qdrant`. The HTTP write path (POST + DELETE) is locked. Phase 7 adds the gRPC read path.

---

## Mission

Build the gRPC search service that wires the locked hybrid retrieval algorithm:

- **Embed query with BGE-M3** via Phase 4's `embed_query()` — one pass produces dense (1024) + sparse (lexical_weights) + ColBERT (1024-per-token).
- **Single Qdrant `query_points()` call** with prefetch (50 dense + 50 sparse, both with `is_active=true` filter) → Weighted RRF fusion (3:1 dense:sparse) → ColBERT multivector rerank → 0.65 score threshold → top-K (default 5, max 20).
- **gRPC service** at `apps/grpc_service/` with `Search()` and `HealthCheck()` RPCs on port 50051.
- **`is_active=true` always enforced** — Phase 6's soft-deleted documents (which had their chunks hard-deleted from Qdrant) cannot leak into search results, AND the filter blocks any future v2 atomic-version-swap pre-flip chunks too.
- **Tenant isolation** via Phase 2's `collection_name()` helper — same scoping discipline as Phases 5/6.

After Phase 7: a bot/agent caller hits `Search()` on `localhost:50051` with `(tenant_id, bot_id, query, top_k, filters)` and gets relevant chunks back in ~50–150 ms (warm). The full v1 architecture from `rag_system_guide.md` Section 3 is realized: HTTP write path + gRPC read path, both isolated to the bot's collection, both enforcing `is_active=true`.

---

## Read first

- `build_prompts/phase_4_embedding_chunking/spec.md` — `embed_query()` produces all three vector types in one pass; `sparse_to_qdrant()` and `colbert_to_qdrant()` are the format converters.
- `build_prompts/phase_4_embedding_chunking/implementation_report.md` — confirms the `devices=[...]` API and other Phase 4 details.
- `build_prompts/phase_3_qdrant_layer/spec.md` — `get_qdrant_client()`, `collection.collection_exists` semantics, retry/backoff via `with_retry`.
- `build_prompts/phase_3_qdrant_layer/implementation_report.md` — qdrant-client API surface confirmed for create/delete operations; Phase 7 verifies `query_points` separately.
- `build_prompts/phase_5b_upload_idempotency/spec.md` — the locked write path; Phase 7 must not regress any of it.
- `build_prompts/phase_2_domain_models/spec.md` — `slug_validator`, `collection_name()` helper.
- `build_prompts/phase_1_foundation/spec.md` — Docker Compose layout; the `grpc` container is currently `sleep infinity`.
- `README.md` — context.
- `rag_system_guide.md` (if present) — §3 "Part B — Query Flow" walks the same algorithm.

---

## Hard constraints

1. **Phases 1–6 are locked** except for these explicit modifications:
   - `Dockerfile` — add a RUN step that compiles proto stubs during image build
   - `docker-compose.yml` — replace the `grpc` service's placeholder command with the real server invocation
   - `scripts/compile_proto.sh` — fill in the protoc command (Phase 1's stub had `echo "no proto files yet"`)
   - `scripts/verify_setup.py` — `--full` adds a Search RPC round-trip
   No other Phase 1–6 file is modified.

2. **No new RUNTIME dependencies.** `grpcio` is in pyproject.toml from Phase 1. `grpcio-tools` is a build-only tool — invoke it via `uvx --from grpcio-tools` in `compile_proto.sh` (so it doesn't bloat the runtime image). The Dockerfile RUN that calls `compile_proto.sh` will pull `grpcio-tools` into the build cache; the final stage only ships `grpcio`.

3. **gRPC service definition is locked** — see the `proto/search.proto` block under "File-by-file specification."

4. **Search algorithm is locked** (no variations in v1):
   - Embed query with BGE-M3 (Phase 4's `embed_query()` — never reach into the model directly).
   - Single `client.query_points()` call with the prefetch + fusion + rerank shape below.
   - `is_active=true` filter inside EVERY prefetch (not on the final query — too late).
   - RRF weights: dense=3.0, sparse=1.0 (Weighted RRF, not plain RRF).
   - score_threshold: 0.65 (final post-rerank score; Qdrant's parameter applied server-side).
   - top_k clamped 1 ≤ k ≤ 20: out-of-range → INVALID_ARGUMENT (don't silently clamp).

5. **Read-only.** Search NEVER writes to Postgres or Qdrant. No auto-create of tenants/bots/collections. If the bot's collection doesn't exist, return `NOT_FOUND`.

6. **`tenant_id` and `bot_id` validation in handler** via Phase 2's `slug_validator`. Bad slugs → INVALID_ARGUMENT before any Qdrant call.

7. **`only_active=false` in `Filters` is rejected** with INVALID_ARGUMENT. v1 only serves active chunks.

8. **Empty query string** (or whitespace-only) → INVALID_ARGUMENT. Don't pass to the embedder.

9. **Generated stubs live at `apps/grpc_service/generated/`.** Already in `.gitignore` from Phase 1. Regenerated at image-build time via `compile_proto.sh`. Local dev must run `bash scripts/compile_proto.sh` after editing `proto/search.proto`.

10. **The gRPC server runs as the grpc Compose service.** The container's command becomes `uv run python -m apps.grpc_service.server` (or equivalent). The `sleep infinity` placeholder from Phase 1 is gone.

11. **No gRPC reflection in v1.** grpcurl users target the .proto file directly. Reflection is a Phase 8 nice-to-have.

12. **No code comments unless spec or invariant justifies. No emoji. No `*.md` beyond `implementation_report.md`.**

13. **gRPC server uses ThreadPoolExecutor (default 10 workers) for handlers.** Each thread shares the process's BGE-M3 singleton. The first Search RPC pays ~30s for cold model load; operators warm via `verify_setup.py --full` post-deploy.

---

## API contract (locked)

**proto/search.proto** is the source of truth. Service: `qdrant_rag.v1.VectorSearch`. Two RPCs.

**`Search(SearchRequest) returns (SearchResponse)`:**

Request:
- `tenant_id` (string, required, slug regex)
- `bot_id` (string, required, slug regex)
- `query` (string, required, non-empty after strip)
- `top_k` (int32, optional, default 5, range [1, 20])
- `filters` (Filters message, optional)

Filters:
- `source_types` (repeated string) — OR within field, AND across fields
- `tags` (repeated string) — OR within field
- `category` (string) — empty means no filter
- `only_active` (bool) — must be true; false → INVALID_ARGUMENT

Response:
- `chunks` (repeated Chunk)
- `total_candidates` (int32) — count after rerank, before threshold gate
- `threshold_used` (float) — 0.65

Chunk fields: `chunk_id`, `doc_id`, `text`, `source_type`, `source_filename`, `source_url`, `section_title`, `section_path`, `page_number`, `category`, `tags`, `score`.

**`HealthCheck(HealthCheckRequest) returns (HealthCheckResponse)`:**

Response: `qdrant_ok` (bool), `embedder_loaded` (bool), `version` (string).

**gRPC status codes:**

| Status | When |
|---|---|
| OK | Search succeeded; chunks may be `[]` (all results filtered by threshold) |
| INVALID_ARGUMENT | bad slug · empty query · top_k out of [1, 20] · `filters.only_active=false` |
| NOT_FOUND | bot's Qdrant collection doesn't exist (no upload has happened yet) |
| UNAVAILABLE | Qdrant transient error (after Phase 3's retry exhausts) |
| INTERNAL | embedder load failure · unexpected exception |

---

## Deliverables

```
qdrant_rag/
├── proto/
│   └── search.proto                           ← NEW
├── apps/grpc_service/
│   ├── __init__.py                            ← UNCHANGED stub
│   ├── apps.py                                ← UNCHANGED stub
│   ├── generated/                             ← NEW dir (gitignored)
│   │   ├── __init__.py                        ← NEW (empty package marker)
│   │   ├── search_pb2.py                      ← GENERATED (gitignored)
│   │   └── search_pb2_grpc.py                 ← GENERATED (gitignored)
│   ├── server.py                              ← NEW (gRPC server entrypoint)
│   └── handler.py                             ← NEW (SearchService impl)
├── apps/qdrant_core/
│   └── search.py                              ← NEW (hybrid query builder)
├── scripts/
│   ├── compile_proto.sh                       ← FILL IN (Phase 1 stub)
│   └── verify_setup.py                        ← EXTEND (--full adds Search RPC)
├── Dockerfile                                 ← MODIFY (RUN compile_proto.sh)
├── docker-compose.yml                         ← MODIFY (grpc service command)
└── tests/
    ├── test_search_query.py                   ← NEW (unit; mocked Qdrant)
    └── test_search_grpc.py                    ← NEW (integration; real Qdrant + grpc)
```

7 new + 3 modified + 2 generated (built by image; not in git) = 10 hand-edited files.

---

## File-by-file specification

### `proto/search.proto` (NEW)

```protobuf
syntax = "proto3";

package qdrant_rag.v1;

service VectorSearch {
  rpc Search(SearchRequest) returns (SearchResponse);
  rpc HealthCheck(HealthCheckRequest) returns (HealthCheckResponse);
}

message SearchRequest {
  string tenant_id = 1;
  string bot_id = 2;
  string query = 3;
  int32 top_k = 4;
  Filters filters = 5;
}

message Filters {
  repeated string source_types = 1;
  repeated string tags = 2;
  string category = 3;
  bool only_active = 4;
}

message SearchResponse {
  repeated Chunk chunks = 1;
  int32 total_candidates = 2;
  float threshold_used = 3;
}

message Chunk {
  string chunk_id = 1;
  string doc_id = 2;
  string text = 3;
  string source_type = 4;
  string source_filename = 5;
  string source_url = 6;
  string section_title = 7;
  repeated string section_path = 8;
  int32 page_number = 9;
  string category = 10;
  repeated string tags = 11;
  float score = 12;
}

message HealthCheckRequest {}

message HealthCheckResponse {
  bool qdrant_ok = 1;
  bool embedder_loaded = 2;
  string version = 3;
}
```

### `scripts/compile_proto.sh` (FILL IN)

Replace Phase 1's stub with:

```bash
#!/usr/bin/env bash
set -euo pipefail

PROTO_DIR="proto"
OUT_DIR="apps/grpc_service/generated"

mkdir -p "$OUT_DIR"
touch "$OUT_DIR/__init__.py"

uv run python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  "$PROTO_DIR/search.proto"

# Fix the generated grpc stub's import to be package-relative.
# Default protoc generates `import search_pb2`; we need `from . import search_pb2`.
GRPC_FILE="$OUT_DIR/search_pb2_grpc.py"
if [ -f "$GRPC_FILE" ]; then
  sed -i 's/^import search_pb2/from . import search_pb2/' "$GRPC_FILE"
fi

echo "[compile_proto] Stubs generated in $OUT_DIR"
```

The `sed` line patches a known protoc quirk: generated grpc files use a non-package-relative import that breaks when the file lives inside a package. `from . import search_pb2` is the fix.

Make it executable: `chmod +x scripts/compile_proto.sh`.

### `apps/grpc_service/generated/__init__.py` (NEW — package marker)

Just an empty file. Without it, `apps.grpc_service.generated` isn't an importable package.

### `apps/qdrant_core/search.py` (NEW — hybrid query builder)

The pure logic of building the query — no gRPC concerns. The handler imports this and translates between gRPC types and dicts.

```python
"""Hybrid search query builder.

Encapsulates the locked retrieval algorithm: embed query → single
client.query_points() with prefetch (50 dense + 50 sparse) → Weighted
RRF (3:1 dense:sparse) → ColBERT rerank → 0.65 threshold → top-K.

Handler-agnostic: takes plain dicts/strings, returns plain dicts.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client.models import (
    FieldCondition,
    Filter,
    FusionQuery,
    Fusion,
    MatchAny,
    MatchValue,
    NamedVector,
    NamedSparseVector,
    Prefetch,
    SparseVector,
)

from apps.ingestion.embedder import (
    DENSE_DIM,
    colbert_to_qdrant,
    embed_query,
    sparse_to_qdrant,
)
from apps.qdrant_core.client import get_qdrant_client, with_retry
from apps.qdrant_core.collection import (
    COLBERT_VECTOR_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
)
from apps.qdrant_core.exceptions import QdrantOperationError
from apps.qdrant_core.naming import collection_name

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
MAX_TOP_K = 20
SCORE_THRESHOLD = 0.65
PREFETCH_LIMIT = 50
RRF_DENSE_WEIGHT = 3.0
RRF_SPARSE_WEIGHT = 1.0


def search(
    *,
    tenant_id: str,
    bot_id: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    source_types: list[str] | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Run the locked hybrid search.

    Returns a dict with:
      - chunks: list of payload dicts, each with `score` added
      - total_candidates: number of points after rerank, before threshold (informational)
      - threshold_used: SCORE_THRESHOLD
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()

    if not client.collection_exists(name):
        raise CollectionNotFoundError(f"Collection {name!r} does not exist.")

    embeddings = embed_query(query)
    dense_vec = embeddings["dense"].tolist() if hasattr(embeddings["dense"], "tolist") else list(embeddings["dense"])
    sparse_qd = sparse_to_qdrant(embeddings["sparse"])
    colbert_vec = colbert_to_qdrant(embeddings["colbert"])

    # Build the filter applied inside both prefetches and the final query.
    must_conditions: list[FieldCondition] = [
        FieldCondition(key="is_active", match=MatchValue(value=True)),
    ]
    if source_types:
        must_conditions.append(
            FieldCondition(key="source_type", match=MatchAny(any=source_types))
        )
    if tags:
        must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=tags)))
    if category:
        must_conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))

    qfilter = Filter(must=must_conditions)

    prefetch = [
        Prefetch(
            query=dense_vec,
            using=DENSE_VECTOR_NAME,
            limit=PREFETCH_LIMIT,
            filter=qfilter,
        ),
        Prefetch(
            query=SparseVector(indices=sparse_qd["indices"], values=sparse_qd["values"]),
            using=SPARSE_VECTOR_NAME,
            limit=PREFETCH_LIMIT,
            filter=qfilter,
        ),
    ]

    return _execute_query(
        client=client,
        collection=name,
        prefetch=prefetch,
        colbert_vec=colbert_vec,
        qfilter=qfilter,
        top_k=top_k,
    )


@with_retry()
def _execute_query(
    *,
    client,
    collection: str,
    prefetch: list,
    colbert_vec,
    qfilter,
    top_k: int,
) -> dict[str, Any]:
    """The actual Qdrant call. Wrapped in retry decorator for transient failures."""
    # The shape of this call may need API-verification adjustments at
    # implementation time. The intent: prefetch dense + sparse with RRF
    # fusion, then rerank with ColBERT multivector.
    response = client.query_points(
        collection_name=collection,
        prefetch=prefetch,
        # Stage 1: fuse the two prefetches with Weighted RRF.
        # Stage 2 (final): rerank with ColBERT vector.
        # The exact API for chaining stages may differ between qdrant-client
        # versions — verify at implementation time.
        query=colbert_vec,
        using=COLBERT_VECTOR_NAME,
        limit=top_k,
        score_threshold=SCORE_THRESHOLD,
        with_payload=True,
        query_filter=qfilter,
    )

    points = response.points
    chunks = []
    for p in points:
        payload = dict(p.payload or {})
        payload["score"] = float(p.score)
        chunks.append(payload)

    return {
        "chunks": chunks,
        "total_candidates": len(points),
        "threshold_used": SCORE_THRESHOLD,
    }


class CollectionNotFoundError(QdrantOperationError):
    """Raised when a search targets a bot whose collection doesn't exist."""
```

**IMPORTANT:** the spec sketches one possible shape for the `query_points()` call. The installed `qdrant-client` version's actual API for "prefetch + RRF fusion + multivector rerank in one call" must be verified before writing this file. The implementing agent runs an inspection step (similar to Phase 3) and adapts the call shape while preserving algorithm SEMANTICS:

- 50 dense + 50 sparse prefetch
- Weighted RRF fusion (3:1 dense:sparse)
- ColBERT rerank on the fused candidates
- 0.65 score threshold applied post-rerank
- top_k limit
- `is_active=true` filter inside both prefetches AND the final query

If the installed API doesn't support all stages in one call (older qdrant-client versions), the agent must implement the equivalent via two calls: prefetch + fusion (one call returning candidate IDs), then rerank (second call with those IDs as a filter). Document this fallback in the implementation report.

### `apps/grpc_service/handler.py` (NEW)

The gRPC service implementation. Translates between proto messages and `apps.qdrant_core.search`.

```python
"""SearchService gRPC handler."""

from __future__ import annotations

import logging
import time

import grpc

from apps.grpc_service.generated import search_pb2, search_pb2_grpc
from apps.qdrant_core.search import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    SCORE_THRESHOLD,
    CollectionNotFoundError,
    search,
)
from apps.qdrant_core.exceptions import QdrantConnectionError, QdrantError
from apps.tenants.validators import InvalidIdentifierError, validate_slug

logger = logging.getLogger(__name__)

VERSION = "0.1.0-dev"


class VectorSearchService(search_pb2_grpc.VectorSearchServicer):
    def Search(self, request, context):
        started = time.monotonic()

        # Slug validation
        try:
            validate_slug(request.tenant_id, field_name="tenant_id")
            validate_slug(request.bot_id, field_name="bot_id")
        except InvalidIdentifierError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        # Query validation
        query = (request.query or "").strip()
        if not query:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Query must be non-empty.")

        # top_k validation
        top_k = request.top_k or DEFAULT_TOP_K
        if top_k < 1 or top_k > MAX_TOP_K:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"top_k must be in [1, {MAX_TOP_K}], got {top_k}.",
            )

        # Filters validation
        if request.filters and not request.filters.only_active:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "filters.only_active must be true in v1.",
            )

        source_types = list(request.filters.source_types) if request.filters else None
        tags = list(request.filters.tags) if request.filters else None
        category = request.filters.category if (request.filters and request.filters.category) else None

        try:
            result = search(
                tenant_id=request.tenant_id,
                bot_id=request.bot_id,
                query=query,
                top_k=top_k,
                source_types=source_types,
                tags=tags,
                category=category,
            )
        except CollectionNotFoundError:
            context.abort(grpc.StatusCode.NOT_FOUND, "Collection does not exist for this tenant/bot.")
        except QdrantConnectionError as exc:
            logger.warning("search_qdrant_unavailable", extra={"error": str(exc)})
            context.abort(grpc.StatusCode.UNAVAILABLE, f"Qdrant unavailable: {exc}")
        except QdrantError as exc:
            logger.error("search_qdrant_error", extra={"error": str(exc)}, exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, f"Qdrant error: {exc}")
        except Exception as exc:
            logger.error("search_unexpected_error", extra={"error": str(exc)}, exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, f"Unexpected error: {exc}")

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "search_succeeded",
            extra={
                "tenant_id": request.tenant_id,
                "bot_id": request.bot_id,
                "query_length": len(query),
                "top_k_requested": top_k,
                "results_returned": len(result["chunks"]),
                "total_candidates": result["total_candidates"],
                "threshold_used": result["threshold_used"],
                "elapsed_ms": elapsed_ms,
            },
        )

        response = search_pb2.SearchResponse(
            total_candidates=result["total_candidates"],
            threshold_used=result["threshold_used"],
        )
        for chunk_dict in result["chunks"]:
            chunk_msg = search_pb2.Chunk(
                chunk_id=chunk_dict.get("chunk_id", ""),
                doc_id=chunk_dict.get("doc_id", ""),
                text=chunk_dict.get("text", ""),
                source_type=chunk_dict.get("source_type", ""),
                source_filename=chunk_dict.get("source_filename") or "",
                source_url=chunk_dict.get("source_url") or "",
                section_title=chunk_dict.get("section_title") or "",
                section_path=list(chunk_dict.get("section_path") or []),
                page_number=chunk_dict.get("page_number") or 0,
                category=chunk_dict.get("category") or "",
                tags=list(chunk_dict.get("tags") or []),
                score=float(chunk_dict.get("score", 0.0)),
            )
            response.chunks.append(chunk_msg)
        return response

    def HealthCheck(self, request, context):
        from apps.ingestion.embedder import _get_model
        from apps.qdrant_core.client import get_qdrant_client

        # Embedder loaded? Check the lru_cache without forcing load.
        embedder_loaded = _get_model.cache_info().currsize > 0

        # Qdrant ok?
        qdrant_ok = False
        try:
            get_qdrant_client().get_collections()
            qdrant_ok = True
        except Exception:
            pass

        return search_pb2.HealthCheckResponse(
            qdrant_ok=qdrant_ok,
            embedder_loaded=embedder_loaded,
            version=VERSION,
        )
```

### `apps/grpc_service/server.py` (NEW)

```python
"""gRPC server entrypoint.

Run with: uv run python -m apps.grpc_service.server
"""

from __future__ import annotations

import logging
import os
import signal
from concurrent import futures

import django
import grpc

# Configure Django before importing anything that touches settings.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.grpc_service.generated import search_pb2_grpc  # noqa: E402
from apps.grpc_service.handler import VectorSearchService  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_PORT = 50051
DEFAULT_MAX_WORKERS = 10
GRACEFUL_SHUTDOWN_S = 10


def serve() -> None:
    port = int(os.environ.get("GRPC_PORT", DEFAULT_PORT))
    max_workers = int(os.environ.get("GRPC_MAX_WORKERS", DEFAULT_MAX_WORKERS))

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    search_pb2_grpc.add_VectorSearchServicer_to_server(VectorSearchService(), server)
    bind_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(bind_addr)
    server.start()
    logger.info("grpc_server_started", extra={"port": port, "workers": max_workers})

    def _shutdown(signum, frame):
        logger.info("grpc_server_shutdown", extra={"signal": signum})
        stop_event = server.stop(grace=GRACEFUL_SHUTDOWN_S)
        stop_event.wait()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.wait_for_termination()


if __name__ == "__main__":
    serve()
```

### `Dockerfile` (MODIFY — add stub generation step)

After the existing `COPY . .` step (or wherever the proto file lands in the image), add:

```dockerfile
# Compile proto stubs for the gRPC service.
RUN bash scripts/compile_proto.sh
```

This runs at image build, baking the generated stubs into the image so the grpc container can import them at startup.

Verify: after build, `docker compose exec grpc python -c "from apps.grpc_service.generated import search_pb2; print('ok')"` returns "ok".

### `docker-compose.yml` (MODIFY — replace grpc command)

Replace the existing grpc service `command:` (currently `sh -c "echo 'gRPC service not implemented yet (Phase 7).'..."`) with:

```yaml
  grpc:
    build: .
    container_name: qdrant_rag_grpc
    command:
      - sh
      - -c
      - >-
          python -m apps.grpc_service.server
    env_file: .env
    depends_on:
      web: {condition: service_healthy}
    ports:
      - "${GRPC_PORT:-50051}:50051"
    volumes:
      - bge_cache:/app/.cache/bge
    restart: unless-stopped
    networks: [qdrant_rag_net]
```

Use the YAML list-form `command:` per the locked pattern from Phase 1's pitfall #14a (folded scalars dropping flags). Single line in the script means no folding ambiguity.

### `scripts/verify_setup.py` (EXTEND)

After Phase 4's `warmup_embedder()` call (only when `--full`), add a Search RPC round-trip:

```python
def search_roundtrip() -> None:
    """Issue a Search RPC against the running gRPC service.

    Requires: the grpc container is up, the web container is up,
    and BGE-M3 is loadable. Creates a throwaway tenant/bot/doc,
    uploads via the HTTP API, searches, and tears down.
    """
    import uuid as _uuid
    import time as _time
    import grpc as _grpc

    from apps.grpc_service.generated import search_pb2, search_pb2_grpc

    grpc_host = os.environ.get("GRPC_HOST", "localhost")
    grpc_port = int(os.environ.get("GRPC_PORT", "50051"))
    addr = f"{grpc_host}:{grpc_port}"

    print(f"[verify_setup --full] Connecting to gRPC at {addr} ...")
    channel = _grpc.insecure_channel(addr)
    try:
        stub = search_pb2_grpc.VectorSearchStub(channel)
        # First call HealthCheck
        hc = stub.HealthCheck(search_pb2.HealthCheckRequest(), timeout=5)
        print(f"[verify_setup --full] HealthCheck: qdrant_ok={hc.qdrant_ok}, embedder_loaded={hc.embedder_loaded}")
        # NOTE: a true end-to-end search requires an upload first. For the
        # --full check we only verify HealthCheck + Search returns INVALID_ARGUMENT
        # for an empty query (proves the server is wired up).
        try:
            stub.Search(search_pb2.SearchRequest(tenant_id="x", bot_id="y", query=""), timeout=5)
            raise SystemExit("[verify_setup --full] Expected INVALID_ARGUMENT for empty query, got OK")
        except _grpc.RpcError as exc:
            if exc.code() != _grpc.StatusCode.INVALID_ARGUMENT:
                raise SystemExit(f"[verify_setup --full] Expected INVALID_ARGUMENT, got {exc.code()}")
        print("[verify_setup --full] Search round-trip succeeded.")
    finally:
        channel.close()


def main() -> None:
    parser = argparse.ArgumentParser(...)
    # ... existing args ...
    args = parser.parse_args()

    ping_postgres()
    ping_qdrant()
    if args.full:
        roundtrip_qdrant_collection()
        warmup_embedder()
        search_roundtrip()
    print("[verify_setup] All checks passed.")
```

Phase 1's default behavior + Phase 3's collection round-trip + Phase 4's embedder warmup are all preserved.

### `tests/test_search_query.py` (NEW)

Unit tests for `apps.qdrant_core.search` with mocked Qdrant client. Verifies the query shape: prefetch counts, RRF weights (where applicable), threshold, filter composition.

```python
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from apps.qdrant_core.search import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    PREFETCH_LIMIT,
    SCORE_THRESHOLD,
    CollectionNotFoundError,
    search,
)


@pytest.fixture
def mock_deps():
    with (
        patch("apps.qdrant_core.search.get_qdrant_client") as client_mock,
        patch("apps.qdrant_core.search.embed_query") as embed_mock,
    ):
        client = client_mock.return_value
        client.collection_exists.return_value = True
        client.query_points.return_value = MagicMock(points=[])

        embed_mock.return_value = {
            "dense": np.zeros(1024, dtype=np.float32),
            "sparse": {"42": 0.5},
            "colbert": np.zeros((3, 1024), dtype=np.float32),
        }
        yield {"client": client, "embed": embed_mock}


class TestSearchHappyPath:
    def test_calls_qdrant_with_correct_collection_name(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        called_kwargs = mock_deps["client"].query_points.call_args.kwargs
        assert called_kwargs["collection_name"] == "t_test_t__b_test_b"

    def test_uses_default_top_k(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        called_kwargs = mock_deps["client"].query_points.call_args.kwargs
        assert called_kwargs["limit"] == DEFAULT_TOP_K

    def test_score_threshold_is_065(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        called_kwargs = mock_deps["client"].query_points.call_args.kwargs
        assert called_kwargs["score_threshold"] == SCORE_THRESHOLD

    def test_two_prefetches(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        called_kwargs = mock_deps["client"].query_points.call_args.kwargs
        assert len(called_kwargs["prefetch"]) == 2
        for pf in called_kwargs["prefetch"]:
            assert pf.limit == PREFETCH_LIMIT


class TestCollectionNotFound:
    def test_raises_when_collection_missing(self, mock_deps):
        mock_deps["client"].collection_exists.return_value = False
        with pytest.raises(CollectionNotFoundError):
            search(tenant_id="test_t", bot_id="test_b", query="hello")
```

### `tests/test_search_grpc.py` (NEW)

Integration tests via a Python gRPC client targeting `localhost:50051`. Skip-not-fail if the server is unreachable.

```python
import json
import pathlib
import uuid

import grpc
import pytest
from rest_framework.test import APIClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def grpc_channel():
    """Connect to the running gRPC server. Skip the suite if unreachable."""
    channel = grpc.insecure_channel("localhost:50051")
    try:
        # 5s timeout to confirm server is up.
        grpc.channel_ready_future(channel).result(timeout=5)
    except grpc.FutureTimeoutError:
        pytest.skip("gRPC server not reachable on localhost:50051")
    yield channel
    channel.close()


@pytest.fixture(scope="session")
def search_stub(grpc_channel):
    from apps.grpc_service.generated import search_pb2_grpc
    return search_pb2_grpc.VectorSearchStub(grpc_channel)


@pytest.fixture
def fresh_bot():
    tenant = f"test_t_{uuid.uuid4().hex[:8]}"
    bot = f"test_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    try:
        from apps.qdrant_core.collection import drop_collection
        drop_collection(tenant, bot)
    except Exception:
        pass


@pytest.fixture
def http_client():
    return APIClient()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def uploaded_doc(http_client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    r = http_client.post(
        f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json"
    )
    assert r.status_code == 201, r.json()
    return tenant, bot, body["doc_id"]


@pytest.mark.django_db(transaction=True)
class TestSearchHappyPath:
    def test_search_returns_relevant_chunks(self, search_stub, uploaded_doc):
        from apps.grpc_service.generated import search_pb2

        tenant, bot, _doc_id = uploaded_doc
        request = search_pb2.SearchRequest(
            tenant_id=tenant, bot_id=bot, query="cold pizza refund", top_k=5
        )
        # filters defaults: only_active=False (proto default for bool is false).
        # This SHOULD return INVALID_ARGUMENT per the spec — empty filters
        # use the default which has only_active=false. Test the alternative:
        # send a Filters with only_active=true.
        request.filters.only_active = True

        response = search_stub.Search(request, timeout=10)
        assert response.threshold_used > 0
        # Don't strictly assert chunks > 0 — model + small fixture might
        # not always cross threshold. But response shape should be valid.
        # Note: Chunk message has NO tenant_id/bot_id fields (caller already
        # specified them in the request — no need to echo back).
        for chunk in response.chunks:
            assert chunk.score >= 0.65
            assert chunk.chunk_id  # non-empty


class TestSearchValidation:
    def test_invalid_argument_on_empty_query(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        request = search_pb2.SearchRequest(tenant_id="t1", bot_id="b1", query="")
        request.filters.only_active = True
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_bad_slug(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        request = search_pb2.SearchRequest(
            tenant_id="Bad-Tenant", bot_id="b1", query="x"
        )
        request.filters.only_active = True
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_top_k_too_high(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        request = search_pb2.SearchRequest(
            tenant_id="t1", bot_id="b1", query="x", top_k=999
        )
        request.filters.only_active = True
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_when_only_active_false(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        request = search_pb2.SearchRequest(
            tenant_id="t1", bot_id="b1", query="x"
        )
        request.filters.only_active = False
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


class TestSearchNotFound:
    def test_not_found_when_collection_missing(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        request = search_pb2.SearchRequest(
            tenant_id=f"t_{uuid.uuid4().hex[:8]}",
            bot_id=f"b_{uuid.uuid4().hex[:8]}",
            query="hello",
        )
        request.filters.only_active = True
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


class TestHealthCheck:
    def test_health_check_returns_versioned_response(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        response = search_stub.HealthCheck(search_pb2.HealthCheckRequest(), timeout=5)
        assert response.version == "0.1.0-dev"
        # qdrant_ok should be True if Compose stack is up
        assert response.qdrant_ok is True


class TestCrossTenantIsolation:
    def test_search_in_tenant_b_does_not_see_tenant_a_chunks(
        self, search_stub, http_client, fresh_bot
    ):
        from apps.grpc_service.generated import search_pb2

        tenant_a, bot_a = fresh_bot
        body = _load("valid_pdf_doc.json")
        body["doc_id"] = str(uuid.uuid4())
        r = http_client.post(
            f"/v1/tenants/{tenant_a}/bots/{bot_a}/documents", body, format="json"
        )
        assert r.status_code == 201

        # Search in a different tenant — collection doesn't exist → NOT_FOUND
        tenant_b = f"t_{uuid.uuid4().hex[:8]}"
        bot_b = f"b_{uuid.uuid4().hex[:8]}"
        request = search_pb2.SearchRequest(
            tenant_id=tenant_b, bot_id=bot_b, query="cold pizza"
        )
        request.filters.only_active = True
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
```

---

## Acceptance criteria

Phase 7 is complete when **all** of these pass:

1. `bash scripts/compile_proto.sh` exits 0 and creates `apps/grpc_service/generated/{search_pb2.py, search_pb2_grpc.py}`.
2. `uv run ruff check .` — zero violations.
3. `uv run ruff format --check .` — zero changes.
4. `uv run python manage.py check` — exits 0.
5. Stack rebuild: `make down && make up && sleep 90 && make health` — green JSON; all containers healthy or running including grpc (now serving, not `sleep infinity`).
6. From host, with stack up: `grpcurl -plaintext -import-path proto/ -proto search.proto -d '{"tenant_id": "x", "bot_id": "y", "query": "", "filters": {"only_active": true}}' localhost:50051 qdrant_rag.v1.VectorSearch/Search` → returns `INVALID_ARGUMENT`.
7. `docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full` — exits 0; HealthCheck and Search RPCs both succeed.
8. `uv run pytest tests/test_search_query.py -v` — green (mocked Qdrant; fast).
9. `docker compose -f docker-compose.yml exec web pytest tests/test_search_grpc.py -v` — green (real gRPC + Qdrant + warm embedder).
10. Phase 1+2+3+4+5a+5b+6 regression: full host suite `uv run pytest -v` keeps all 118+ prior tests green; `make health` still 200; HTTP upload + delete still work.

---

## Common pitfalls

1. **qdrant-client API drift on `query_points()`.** The exact call shape (prefetch + fusion + multivector rerank in one call) differs between client versions. The implementation phase MUST verify with `inspect.signature(client.query_points)` and `Prefetch.__doc__` BEFORE writing search.py. If the version doesn't support all stages in one call, fall back to a two-call pattern (prefetch+fusion, then rerank against the fused IDs).

2. **Fusion weights location.** `Fusion.RRF` may not directly take weights in some versions; weights might go on `Prefetch.score_boost` or a separate `WeightedFusion` class. Verify.

3. **Generated stub import path.** `search_pb2_grpc.py` imports `search_pb2` non-relatively by default. The `compile_proto.sh` sed step fixes this. Without it, import fails inside the package.

4. **`is_active=true` filter on FINAL query but not prefetches.** This is a subtle bug: prefetch returns 50 candidates ignoring `is_active`, then the final query filters them out, leaving < 50 for rerank. Always put the filter inside EACH prefetch.

5. **Empty `chunks` response interpreted as failure.** Empty `chunks` with `total_candidates=0` is valid (all results below threshold). Don't return NOT_FOUND or INVALID_ARGUMENT — return OK with empty list.

6. **gRPC ThreadPoolExecutor + BGE-M3 fork.** Server is single-process with multiple threads (not forked workers). The lazy embedder singleton works fine here; first thread to call `embed_query` loads the model, subsequent threads share it.

7. **Server doesn't `django.setup()` before importing modules.** `apps.qdrant_core.search` imports from `apps.tenants.validators` (which imports Django). Without `django.setup()`, `AppRegistryNotReady`. Server.py does `django.setup()` first thing.

8. **`grpc.insecure_channel("localhost:50051")` from host shell vs container.** Tests running on host need port 50051 exposed (it is, per Compose). Tests running inside the web container can use `grpc:50051` (container DNS) OR `localhost:50051` if the test runs in the grpc container. The test fixture uses `localhost:50051` and works from host shell.

9. **`request.filters.only_active` defaults to `false` for proto3 bool.** Proto3 has no field presence for primitives; bool defaults to false. Clients MUST set `only_active=true` explicitly. The handler rejects `false` (which is also the default), so clients sending no filters at all hit INVALID_ARGUMENT. Document this clearly. Alternative: invert — `only_inactive=false` (default false). v1: keep `only_active=true` and require explicit set; clients learn quickly.

10. **`Chunk.page_number` field type.** Proto3 int32 default is 0, not None. If the chunk's payload has `page_number=None` (URL doc with no page), the response's `page_number=0`. Document that `page_number=0` means "absent" by convention.

---

## Out of scope for Phase 7

- gRPC reflection — Phase 8 nice-to-have
- Standard `grpc.health.v1.Health` protocol — Phase 8 (alongside the custom HealthCheck)
- Streaming responses — never (top_k <= 20 fits unary)
- Authentication / API keys — TBD (post-v1)
- Query rewriting / spell-correction — never in v1
- MMR diversification on top-K — v5
- Dynamic per-query threshold — v5
- External cross-encoder reranker (separate service) — v6
- Caching (Redis semantic cache) — v3
- Audit log of search queries — v3
- Metrics / Prometheus — Phase 8

---

## When you finish

1. Confirm all 10 acceptance criteria pass.
2. Commit:
   - `proto/search.proto` (new)
   - `apps/grpc_service/{server,handler}.py` (new)
   - `apps/grpc_service/generated/__init__.py` (new — empty package marker)
   - `apps/qdrant_core/search.py` (new)
   - `scripts/compile_proto.sh` (filled in)
   - `scripts/verify_setup.py` (extended)
   - `Dockerfile` (modified)
   - `docker-compose.yml` (modified)
   - `tests/test_search_query.py` (new)
   - `tests/test_search_grpc.py` (new)
   - `build_prompts/phase_7_search_grpc/implementation_report.md`
   - DO NOT commit `apps/grpc_service/generated/{search_pb2.py, search_pb2_grpc.py}` — they're gitignored.
3. Verify NO Phase 1-6 file modified outside the 3 explicitly-modified ones (Dockerfile, docker-compose.yml, scripts/{compile_proto.sh, verify_setup.py}).
4. Output a short report.

That's Phase 7. Phase 8 (Hardening & Ship) is the final phase: Prometheus metrics, structured-logging enrichment, graceful shutdown verified under SIGTERM, CI green, snapshot/backup scripts, runbook.
