# Phase 3 — Qdrant Layer

> **Audience:** A coding agent (e.g. Claude Code) building on top of verified-green Phase 1 and Phase 2 at `/home/bol7/Documents/BOL7/Qdrant`. Do not modify Phase 1 or Phase 2 deliverables except where this spec explicitly says so.

---

## Mission

Build the Qdrant integration layer:

- **Singleton gRPC `QdrantClient`** at `apps/qdrant_core/client.py` — lazy module-level, retry/backoff on transient failures, `prefer_grpc=True`, API key from settings.
- **Per-bot collection factory** at `apps/qdrant_core/collection.py` — creates collections with the full vector schema (dense 1024 + sparse `bm25` + ColBERT 1024-per-token), all 8 payload indexes, idempotent `get_or_create_collection()`, schema-drift detection that raises a typed error rather than silently recreating.
- **Helpers used by Phase 5/6/7**:
  - `create_collection_for_bot(tenant_id, bot_id) -> str` — returns the collection name on success.
  - `get_or_create_collection(tenant_id, bot_id) -> str` — idempotent. Verifies schema on existing collections.
  - `delete_by_doc_id(tenant_id, bot_id, doc_id) -> int` — returns the count of points deleted; 0 if collection or doc absent.
  - `drop_collection(tenant_id, bot_id) -> bool` — for tests + Phase 6 "delete entire bot" path (which actually arrives in v5, not v1; the helper just exists for tests + completeness).
- **Typed exceptions** at `apps/qdrant_core/exceptions.py`:
  - `QdrantConnectionError` — transient (timeout, refused, network).
  - `CollectionSchemaMismatchError` — schema drift on an existing collection.
  - `QdrantOperationError` — anything else.
- **Extended `scripts/verify_setup.py`** — adds an opt-in `--full` flag that runs a collection round-trip (create → upsert → retrieve → delete → drop). Phase 1's default behavior (ping Postgres + Qdrant) is preserved exactly.
- **Tests**:
  - `tests/test_qdrant_client.py` — singleton init, retry/backoff, fork-safety smoke (all without hitting a real Qdrant; uses dependency injection or mocking).
  - `tests/test_qdrant_collection.py` — integration tests against the real Qdrant container; skip-not-fail if Qdrant unreachable.

After Phase 3: `apps.qdrant_core.collection.create_collection_for_bot("test", "demo")` from a Django shell creates the correct collection in Qdrant, and `delete_by_doc_id("test", "demo", "<uuid>")` removes its chunks. **No upload pipeline yet (Phase 5), no embeddings (Phase 4), no search (Phase 7).** Phase 3 is pure plumbing the higher phases consume.

---

## Read first

- `build_prompts/phase_1_foundation/spec.md` — locked stack, container layout
- `build_prompts/phase_1_foundation/implementation_report.md` — Phase 1 deliverables
- `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract; `collection_name()` and `advisory_lock_key()` helpers in `apps/qdrant_core/naming.py` exist and are imported here
- `build_prompts/phase_2_domain_models/implementation_report.md` — confirms `Document.bot_ref` rename and Phase 2's schema
- `README.md` — project overview
- `rag_system_guide.md` (if present) — §6 "Running Qdrant on Your Own Server" describes the per-bot vector schema this phase realizes

---

## Hard constraints (read before writing any code)

1. **Phase 1 + Phase 2 are locked.** Do not modify any of their deliverables EXCEPT:
   - `scripts/verify_setup.py` is extended (not replaced) with the `--full` round-trip flag.
   - That's the only Phase 1/2 file touched.

2. **No new dependencies.** `qdrant-client` is already in `pyproject.toml` from Phase 1. Use what's installed.

3. **Vector schema is locked.** Dense 1024-dim cosine HNSW `m=16, ef_construct=128`. Sparse named `bm25` with IDF modifier, `on_disk: false`. ColBERT named `colbert`, **1024-dim per token** (NOT 128), max_sim comparator, HNSW disabled (`m=0`). No quantization.

4. **Payload indexes (8 total, locked):** `doc_id` (keyword), `source_type` (keyword), `source_url` (keyword), `language` (keyword), `tags` (keyword array), `category` (keyword), `is_active` (bool), `tenant_id` (keyword with `is_tenant=True`).

5. **`collection_name()` from Phase 2 is the SOLE constructor of collection-name strings.** Phase 3's helpers accept `(tenant_id, bot_id)` as separate args and call the helper internally. Never hand-construct names. The grep test from Phase 2 stays green.

6. **Schema drift is a HARD ERROR.** `get_or_create_collection()` finds an existing collection → verify its schema matches expected → if drift, raise `CollectionSchemaMismatchError` with the diff. **Never silently drop and recreate** — that's data loss.

7. **`prefer_grpc=True` always.** REST port (6333) is configured for occasional admin/debug use only. Production calls go via gRPC (6334).

8. **Retry only on `QdrantConnectionError`.** Three attempts max, exponential backoff `0.5s → 1.0s → 2.0s` (with ±20% jitter to avoid thundering-herd). Other errors propagate immediately — never retry on schema drift, never retry on a 4xx-equivalent gRPC code.

9. **Singleton client is lazily constructed.** Module-level cached function (functools.lru_cache or equivalent), instantiated on first access. POST-fork construction guarantees gunicorn workers each get their own client (no fork-after-channel-create gRPC pitfalls).

10. **Tests follow the `@pytest.mark.django_db` discipline from Phase 2.** Phase 3's tests don't hit the Django ORM (no Tenant/Bot/Document creation needed), so the marker is OMITTED. Tests that require a real Qdrant gracefully skip with `pytest.skip` if it's unreachable, never fail.

11. **No code comments unless the spec or a non-obvious invariant justifies them.** Default is no comments.

12. **No business endpoints, no embedder, no chunker, no API.** Still only `/healthz` and `/admin/`. Phase 4 owns embeddings, Phase 5 owns the upload API.

---

## Stack & versions (unchanged from Phase 1)

`qdrant-client` (no extras) is the only new package surface. Verify the installed version supports the full Qdrant 1.17 schema features: named vectors, sparse vectors with IDF modifier, multivector configs with comparators, payload-index `is_tenant` flag.

If the installed `qdrant-client` version is older than what Qdrant 1.17.1 expects, raise it as a spec defect — but in practice the version pinned via Phase 1's `uv sync --frozen` is the version we use.

---

## Deliverables

New / modified files added to the Phase 1 + Phase 2 tree:

```
qdrant_rag/
├── apps/qdrant_core/
│   ├── exceptions.py              ← NEW
│   ├── client.py                  ← NEW
│   ├── collection.py              ← NEW
│   └── naming.py                  ← UNCHANGED (from Phase 2)
├── scripts/
│   └── verify_setup.py            ← EXTEND (add --full flag, preserve Phase 1 behavior)
└── tests/
    ├── test_qdrant_client.py      ← NEW
    └── test_qdrant_collection.py  ← NEW
```

That's 5 new files + 1 extended file. No Phase 1/2 file is *modified* (their content is preserved verbatim) except the script extension.

---

## File-by-file specification

### `apps/qdrant_core/exceptions.py` (NEW)

```python
"""Typed exceptions raised by the qdrant_core layer.

Callers in Phase 5/6/7 catch these specifically rather than the broad
`grpc.RpcError` or `qdrant_client` internal exceptions, so the upload
pipeline can react differently to transient connectivity vs. real
schema drift.
"""

from __future__ import annotations


class QdrantError(Exception):
    """Base class for all qdrant_core errors. Never raised directly."""


class QdrantConnectionError(QdrantError):
    """Transient connection failure: timeout, refused, network blip.

    Worth retrying with backoff. After exhausted retries, the wrapped
    exception is the last attempt's error.
    """


class CollectionSchemaMismatchError(QdrantError):
    """An existing collection's schema does not match the expected schema.

    Carries the diff so the operator can decide whether to migrate or
    recreate. NEVER auto-recreate — data loss risk.
    """

    def __init__(self, collection_name: str, diff: dict[str, str]) -> None:
        super().__init__(
            f"Collection {collection_name!r} schema mismatch: {diff}"
        )
        self.collection_name = collection_name
        self.diff = diff


class QdrantOperationError(QdrantError):
    """Any other Qdrant-side failure (4xx-equivalent gRPC status, malformed
    request, internal server error). Not retried.
    """
```

### `apps/qdrant_core/client.py` (NEW)

```python
"""Singleton gRPC QdrantClient with retry/backoff on transient failures.

The client is lazily constructed on first access (POST-fork in gunicorn
workers, avoiding fork-after-channel-create issues with gRPC). Each
worker process has its own client instance.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

import grpc
from django.conf import settings
from qdrant_client import QdrantClient

from apps.qdrant_core.exceptions import (
    QdrantConnectionError,
    QdrantOperationError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@functools.lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Return the process-local QdrantClient singleton.

    Construction is lazy (first call). Subsequent calls in the same
    process return the cached instance. Each forked gunicorn worker
    builds its own.
    """
    cfg = settings.QDRANT
    return QdrantClient(
        host=cfg["HOST"],
        grpc_port=cfg["GRPC_PORT"],
        port=cfg["HTTP_PORT"],
        prefer_grpc=cfg["PREFER_GRPC"],
        api_key=cfg["API_KEY"] or None,
        https=False,
        timeout=10,  # seconds; longer than healthz's 2s for upserts/queries
    )


# Connection-error patterns we retry on.
_RETRYABLE_GRPC_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, grpc.RpcError):
        return getattr(exc, "code", lambda: None)() in _RETRYABLE_GRPC_CODES
    # qdrant-client wraps some HTTP/network errors; treat them as transient too
    name = type(exc).__name__
    return name in {
        "ResponseHandlingException",
        "ConnectionError",
        "TimeoutException",
    }


def with_retry(
    *,
    attempts: int = 3,
    initial_delay: float = 0.5,
    backoff: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: retry on transient connection errors only.

    Schema-mismatch / 4xx-equivalent failures propagate immediately.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = initial_delay
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    if not _is_transient(exc):
                        raise
                    last_exc = exc
                    if attempt == attempts - 1:
                        break
                    sleep_for = delay * (1 + random.uniform(-0.2, 0.2))
                    logger.warning(
                        "qdrant_retry",
                        extra={
                            "attempt": attempt + 1,
                            "max_attempts": attempts,
                            "sleep_s": round(sleep_for, 3),
                            "exc_type": type(exc).__name__,
                        },
                    )
                    time.sleep(sleep_for)
                    delay *= backoff
            raise QdrantConnectionError(
                f"Exhausted {attempts} retry attempts: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator
```

### `apps/qdrant_core/collection.py` (NEW)

The heart of Phase 3. Contains the schema definitions and the four helpers (`create_collection_for_bot`, `get_or_create_collection`, `delete_by_doc_id`, `drop_collection`).

Key structure:

```python
"""Per-bot Qdrant collection management.

The schema is locked: dense (1024 cosine HNSW m=16) + sparse bm25 (IDF) +
ColBERT (1024-per-token, max_sim, HNSW disabled). 8 payload indexes.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    Modifier,
    MultiVectorComparator,
    MultiVectorConfig,
    PayloadSchemaType,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
    KeywordIndexParams,
    Filter,
    FieldCondition,
    MatchValue,
)

from apps.qdrant_core.client import get_qdrant_client, with_retry
from apps.qdrant_core.exceptions import (
    CollectionSchemaMismatchError,
    QdrantOperationError,
)
from apps.qdrant_core.naming import collection_name

logger = logging.getLogger(__name__)


# ─── Locked schema definitions ──────────────────────────────────────

DENSE_VECTOR_NAME = "dense"
DENSE_VECTOR_SIZE = 1024
DENSE_HNSW_M = 16
DENSE_HNSW_EF_CONSTRUCT = 128

SPARSE_VECTOR_NAME = "bm25"

COLBERT_VECTOR_NAME = "colbert"
COLBERT_VECTOR_SIZE = 1024  # NOT 128 — BGE-M3's ColBERT head emits 1024-per-token

# Payload indexes (8). Order matters only for human readability of the spec.
PAYLOAD_INDEXES: list[tuple[str, Any]] = [
    ("doc_id", PayloadSchemaType.KEYWORD),
    ("source_type", PayloadSchemaType.KEYWORD),
    ("source_url", PayloadSchemaType.KEYWORD),
    ("language", PayloadSchemaType.KEYWORD),
    ("tags", PayloadSchemaType.KEYWORD),
    ("category", PayloadSchemaType.KEYWORD),
    ("is_active", PayloadSchemaType.BOOL),
    # tenant_id gets is_tenant=True for Qdrant's tenant-aware storage layout.
    ("tenant_id", KeywordIndexParams(type="keyword", is_tenant=True)),
]


def _expected_vectors_config() -> dict[str, VectorParams]:
    return {
        DENSE_VECTOR_NAME: VectorParams(
            size=DENSE_VECTOR_SIZE,
            distance=Distance.COSINE,
            hnsw_config=HnswConfigDiff(
                m=DENSE_HNSW_M,
                ef_construct=DENSE_HNSW_EF_CONSTRUCT,
            ),
        ),
        COLBERT_VECTOR_NAME: VectorParams(
            size=COLBERT_VECTOR_SIZE,
            distance=Distance.COSINE,
            multivector_config=MultiVectorConfig(
                comparator=MultiVectorComparator.MAX_SIM,
            ),
            hnsw_config=HnswConfigDiff(m=0),  # disabled — rerank-only
        ),
    }


def _expected_sparse_vectors_config() -> dict[str, SparseVectorParams]:
    return {
        SPARSE_VECTOR_NAME: SparseVectorParams(
            index=SparseIndexParams(on_disk=False),
            modifier=Modifier.IDF,
        ),
    }


# ─── Helpers ────────────────────────────────────────────────────────

@with_retry()
def create_collection_for_bot(tenant_id: str, bot_id: str) -> str:
    """Create the per-bot collection with the locked schema.

    Idempotent on Qdrant's "already exists" 409: caller should use
    get_or_create_collection() if they want that semantic. This helper
    raises QdrantOperationError if the collection exists.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()
    client.create_collection(
        collection_name=name,
        vectors_config=_expected_vectors_config(),
        sparse_vectors_config=_expected_sparse_vectors_config(),
    )
    for field_name, schema in PAYLOAD_INDEXES:
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=schema,
        )
    logger.info(
        "qdrant_collection_created",
        extra={"collection_name": name, "tenant_id": tenant_id, "bot_id": bot_id},
    )
    return name


@with_retry()
def get_or_create_collection(tenant_id: str, bot_id: str) -> str:
    """Idempotent: create if missing, verify schema if exists.

    Raises CollectionSchemaMismatchError if an existing collection's
    schema differs from expected.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()

    if client.collection_exists(name):
        diff = _compare_schema(client, name)
        if diff:
            raise CollectionSchemaMismatchError(name, diff)
        return name

    try:
        return create_collection_for_bot(tenant_id, bot_id)
    except UnexpectedResponse as exc:
        # 409: race condition — another worker created it between our
        # exists() check and the create call. Verify and continue.
        if getattr(exc, "status_code", None) == 409:
            diff = _compare_schema(client, name)
            if diff:
                raise CollectionSchemaMismatchError(name, diff) from exc
            return name
        raise QdrantOperationError(f"create_collection failed: {exc}") from exc


def _compare_schema(client: QdrantClient, name: str) -> dict[str, str]:
    """Return a diff dict describing how the actual schema differs from
    the expected one. Empty dict means schemas match.
    """
    info = client.get_collection(name)
    actual = info.config.params
    expected_v = _expected_vectors_config()
    expected_s = _expected_sparse_vectors_config()

    diff: dict[str, str] = {}
    # Verify dense vector
    actual_dense = actual.vectors.get(DENSE_VECTOR_NAME) if actual.vectors else None
    if not actual_dense or actual_dense.size != DENSE_VECTOR_SIZE:
        diff["dense.size"] = (
            f"expected {DENSE_VECTOR_SIZE}, got "
            f"{getattr(actual_dense, 'size', 'MISSING')}"
        )
    if actual_dense and actual_dense.distance != Distance.COSINE:
        diff["dense.distance"] = (
            f"expected COSINE, got {actual_dense.distance}"
        )

    # Verify ColBERT vector size
    actual_colbert = (
        actual.vectors.get(COLBERT_VECTOR_NAME) if actual.vectors else None
    )
    if not actual_colbert or actual_colbert.size != COLBERT_VECTOR_SIZE:
        diff["colbert.size"] = (
            f"expected {COLBERT_VECTOR_SIZE}, got "
            f"{getattr(actual_colbert, 'size', 'MISSING')}"
        )
    if actual_colbert and not actual_colbert.multivector_config:
        diff["colbert.multivector"] = "expected enabled, got disabled"

    # Verify sparse
    actual_sparse = actual.sparse_vectors or {}
    if SPARSE_VECTOR_NAME not in actual_sparse:
        diff[f"sparse.{SPARSE_VECTOR_NAME}"] = "MISSING"
    else:
        sp = actual_sparse[SPARSE_VECTOR_NAME]
        if sp.modifier != Modifier.IDF:
            diff[f"sparse.{SPARSE_VECTOR_NAME}.modifier"] = (
                f"expected IDF, got {sp.modifier}"
            )

    return diff


@with_retry()
def delete_by_doc_id(tenant_id: str, bot_id: str, doc_id: str) -> int:
    """Delete all chunks where payload.doc_id == doc_id.

    Returns the number of points deleted. Returns 0 if collection
    doesn't exist or no points match.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()

    if not client.collection_exists(name):
        return 0

    selector = Filter(
        must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
    )
    # qdrant-client returns a result object whose .operation_id and
    # .status are present; getting the count of deleted points requires
    # a count() before-and-after comparison or relying on the response.
    # For Phase 3, we count first, then delete.
    count_before = client.count(name, count_filter=selector, exact=True).count
    client.delete(collection_name=name, points_selector=selector)
    logger.info(
        "qdrant_chunks_deleted",
        extra={
            "collection_name": name,
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "doc_id": doc_id,
            "count": count_before,
        },
    )
    return count_before


@with_retry()
def drop_collection(tenant_id: str, bot_id: str) -> bool:
    """Drop the entire collection. Returns True if dropped, False if it
    didn't exist. Used by tests + future v5 'delete entire bot' path.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()
    if not client.collection_exists(name):
        return False
    client.delete_collection(name)
    logger.info(
        "qdrant_collection_dropped",
        extra={"collection_name": name, "tenant_id": tenant_id, "bot_id": bot_id},
    )
    return True
```

**IMPORTANT:** The exact qdrant-client API may differ from this sketch — class names, parameter names, and method signatures shift between versions. The implementing agent must verify against the installed version (e.g., `client.collection_exists` vs `client.get_collections` + name check; `count_filter=` vs `filter=`; `KeywordIndexParams` import path) and adapt while preserving the SEMANTICS specified above.

### `scripts/verify_setup.py` (EXTEND)

Phase 1's existing behavior is preserved exactly. Add an opt-in `--full` flag that runs a Qdrant collection round-trip:

```python
import argparse
# ... Phase 1 imports preserved ...


def roundtrip_qdrant_collection() -> None:
    """Create → upsert → retrieve → delete → drop. Verifies the full
    schema is configurable on the live Qdrant.
    """
    import time as _time
    import uuid as _uuid

    from apps.qdrant_core.client import get_qdrant_client
    from apps.qdrant_core.collection import (
        create_collection_for_bot,
        delete_by_doc_id,
        drop_collection,
    )
    from qdrant_client.models import PointStruct, SparseVector

    test_tenant = f"verify_{int(_time.time())}"
    test_bot = "rt"
    test_doc_id = str(_uuid.uuid4())

    print(f"[verify_setup --full] Creating collection for "
          f"tenant={test_tenant!r}, bot={test_bot!r} ...")
    name = create_collection_for_bot(test_tenant, test_bot)
    try:
        client = get_qdrant_client()
        chunk_id = f"{test_doc_id}__i0__c0"
        client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=chunk_id,
                    vector={
                        "dense": [0.0] * 1024,
                        "bm25": SparseVector(indices=[0], values=[0.1]),
                        "colbert": [[0.0] * 1024],
                    },
                    payload={
                        "doc_id": test_doc_id,
                        "tenant_id": test_tenant,
                        "bot_id": test_bot,
                        "is_active": True,
                        "source_type": "verify",
                        "tags": ["verify"],
                        "category": "verify",
                        "language": "en",
                        "source_url": "verify://",
                    },
                )
            ],
        )
        print(f"[verify_setup --full] Upserted dummy point. Deleting by doc_id ...")
        deleted = delete_by_doc_id(test_tenant, test_bot, test_doc_id)
        if deleted != 1:
            raise SystemExit(
                f"[verify_setup --full] Expected to delete 1 point, deleted {deleted}"
            )
        print(f"[verify_setup --full] Round-trip succeeded.")
    finally:
        print(f"[verify_setup --full] Dropping test collection ...")
        drop_collection(test_tenant, test_bot)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify qdrant_rag setup.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run a Qdrant collection round-trip (slower, exercises full schema).",
    )
    args = parser.parse_args()

    # Phase 1 checks (unchanged)
    ping_postgres()
    ping_qdrant()

    if args.full:
        roundtrip_qdrant_collection()

    print("[verify_setup] All checks passed.")


if __name__ == "__main__":
    main()
```

The Phase 1 functions `ping_postgres()` and `ping_qdrant()` are preserved verbatim. Only `main()` and `roundtrip_qdrant_collection()` are new/changed.

### `tests/test_qdrant_client.py` (NEW)

Tests the singleton + retry decorator without hitting a real Qdrant:

```python
import grpc
import pytest

from apps.qdrant_core.client import _is_transient, get_qdrant_client, with_retry
from apps.qdrant_core.exceptions import (
    CollectionSchemaMismatchError,
    QdrantConnectionError,
)


class TestSingleton:
    def test_returns_same_instance(self):
        get_qdrant_client.cache_clear()
        a = get_qdrant_client()
        b = get_qdrant_client()
        assert a is b

    def test_cache_clear_reinitializes(self):
        get_qdrant_client.cache_clear()
        a = get_qdrant_client()
        get_qdrant_client.cache_clear()
        b = get_qdrant_client()
        assert a is not b


class TestIsTransient:
    def test_unavailable_is_transient(self):
        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

        assert _is_transient(FakeRpcError())

    def test_invalid_argument_is_not_transient(self):
        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.INVALID_ARGUMENT

        assert not _is_transient(FakeRpcError())

    def test_value_error_is_not_transient(self):
        assert not _is_transient(ValueError("nope"))


class TestRetryDecorator:
    def test_succeeds_first_try(self):
        calls = []

        @with_retry(attempts=3, initial_delay=0.01)
        def f():
            calls.append(1)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        calls = []

        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

        @with_retry(attempts=3, initial_delay=0.01)
        def f():
            calls.append(1)
            if len(calls) < 2:
                raise FakeRpcError()
            return "ok"

        assert f() == "ok"
        assert len(calls) == 2

    def test_exhausted_retries_raise_connection_error(self):
        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

        @with_retry(attempts=2, initial_delay=0.01)
        def f():
            raise FakeRpcError()

        with pytest.raises(QdrantConnectionError):
            f()

    def test_non_transient_propagates_immediately(self):
        calls = []

        @with_retry(attempts=3, initial_delay=0.01)
        def f():
            calls.append(1)
            raise CollectionSchemaMismatchError("c", {"x": "y"})

        with pytest.raises(CollectionSchemaMismatchError):
            f()
        assert len(calls) == 1
```

### `tests/test_qdrant_collection.py` (NEW)

Integration tests against the real Qdrant container. Each test creates a uniquely-named collection (slug-regex-compliant via `t_test_<uuid8>__b_test_<uuid8>` derivation through the helper) and cleans up in teardown.

Skip-not-fail if Qdrant is unreachable:

```python
import uuid

import pytest

from apps.qdrant_core.client import get_qdrant_client
from apps.qdrant_core.collection import (
    COLBERT_VECTOR_SIZE,
    DENSE_VECTOR_SIZE,
    SPARSE_VECTOR_NAME,
    create_collection_for_bot,
    delete_by_doc_id,
    drop_collection,
    get_or_create_collection,
)
from apps.qdrant_core.exceptions import CollectionSchemaMismatchError


@pytest.fixture(scope="session")
def qdrant_available():
    """Skip the entire integration suite if Qdrant isn't reachable."""
    try:
        get_qdrant_client.cache_clear()
        client = get_qdrant_client()
        client.get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable for integration tests: {exc}")


@pytest.fixture
def fresh_bot(qdrant_available):
    """Yield a (tenant_id, bot_id) pair that doesn't yet exist in Qdrant.
    Drops the collection in teardown.
    """
    tenant = f"test_t_{uuid.uuid4().hex[:8]}"
    bot = f"test_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    try:
        drop_collection(tenant, bot)
    except Exception:
        pass  # best-effort cleanup


class TestCreateCollection:
    def test_create_succeeds_with_locked_schema(self, fresh_bot):
        tenant, bot = fresh_bot
        name = create_collection_for_bot(tenant, bot)
        assert name == f"t_{tenant}__b_{bot}"

        client = get_qdrant_client()
        info = client.get_collection(name)
        # dense
        assert info.config.params.vectors["dense"].size == DENSE_VECTOR_SIZE
        # colbert (1024-per-token, NOT 128)
        assert info.config.params.vectors["colbert"].size == COLBERT_VECTOR_SIZE
        # sparse
        assert SPARSE_VECTOR_NAME in (info.config.params.sparse_vectors or {})

    def test_payload_indexes_exist(self, fresh_bot):
        tenant, bot = fresh_bot
        name = create_collection_for_bot(tenant, bot)
        client = get_qdrant_client()
        info = client.get_collection(name)
        # Confirm indexes were created on doc_id and is_active at minimum
        # (qdrant-client surfaces these via info.payload_schema).
        assert "doc_id" in info.payload_schema
        assert "is_active" in info.payload_schema


class TestGetOrCreateCollection:
    def test_idempotent(self, fresh_bot):
        tenant, bot = fresh_bot
        name1 = get_or_create_collection(tenant, bot)
        name2 = get_or_create_collection(tenant, bot)
        assert name1 == name2

    def test_schema_mismatch_raises(self, fresh_bot):
        # Manually create a collection with a wrong schema, then call
        # get_or_create. It must raise CollectionSchemaMismatchError.
        from qdrant_client.models import Distance, VectorParams
        from apps.qdrant_core.naming import collection_name

        tenant, bot = fresh_bot
        name = collection_name(tenant, bot)
        client = get_qdrant_client()
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(size=512, distance=Distance.COSINE),
            },
        )
        with pytest.raises(CollectionSchemaMismatchError) as exc_info:
            get_or_create_collection(tenant, bot)
        assert "dense.size" in exc_info.value.diff


class TestDeleteByDocId:
    def test_returns_zero_when_collection_missing(self, fresh_bot):
        tenant, bot = fresh_bot
        # Don't create — collection doesn't exist
        assert delete_by_doc_id(tenant, bot, "any-doc-id") == 0

    def test_deletes_only_targeted_doc(self, fresh_bot):
        from qdrant_client.models import PointStruct, SparseVector

        tenant, bot = fresh_bot
        name = create_collection_for_bot(tenant, bot)
        client = get_qdrant_client()

        doc_a = "doc-aaa"
        doc_b = "doc-bbb"

        def _point(doc_id: str, idx: int):
            return PointStruct(
                id=f"{doc_id}__i0__c{idx}",
                vector={
                    "dense": [0.0] * 1024,
                    "bm25": SparseVector(indices=[0], values=[0.1]),
                    "colbert": [[0.0] * 1024],
                },
                payload={"doc_id": doc_id, "is_active": True},
            )

        client.upsert(
            collection_name=name,
            points=[_point(doc_a, 0), _point(doc_a, 1), _point(doc_b, 0)],
        )
        deleted = delete_by_doc_id(tenant, bot, doc_a)
        assert deleted == 2

        # Confirm doc_b survived
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        remaining = client.count(
            name,
            count_filter=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_b))]
            ),
            exact=True,
        )
        assert remaining.count == 1


class TestDropCollection:
    def test_returns_true_when_dropped(self, fresh_bot):
        tenant, bot = fresh_bot
        create_collection_for_bot(tenant, bot)
        assert drop_collection(tenant, bot) is True

    def test_returns_false_when_missing(self, fresh_bot):
        tenant, bot = fresh_bot
        # never created
        assert drop_collection(tenant, bot) is False
```

---

## Schema details (the locked truth)

Cross-reference for the agent. Any deviation here is a spec defect.

### Vector configs

| Name | Type | Size | Distance | HNSW | Multi-vector | Notes |
|---|---|---|---|---|---|---|
| `dense` | dense | **1024** | Cosine | `m=16, ef_construct=128` | — | BGE-M3 dense head |
| `bm25` | sparse | — | — | — | — | IDF modifier, `on_disk: False` |
| `colbert` | dense (multi) | **1024 per token** | Cosine | **`m=0`** (disabled) | `max_sim` comparator | BGE-M3 ColBERT head; rerank-only |

ColBERT being 1024-per-token (not 128) is critical — vanilla ColBERTv2 emits 128-dim per token, but BGE-M3's ColBERT head emits 1024-dim. The collection schema MUST match what BGE-M3 produces in Phase 4. If the agent mis-specifies this and uses 128, every Phase 4+ upsert will fail with a dimension-mismatch error.

### Payload indexes

| Field | Schema | Notes |
|---|---|---|
| `doc_id` | keyword | filter on doc_id is the most common query |
| `source_type` | keyword | pdf / url / csv / faq / image / docx / html |
| `source_url` | keyword | for URL-sourced docs |
| `language` | keyword | en / es / etc. |
| `tags` | keyword (array) | from `custom_metadata.tags` |
| `category` | keyword | from `custom_metadata.category` |
| `is_active` | bool | search filter — `is_active=true` is always applied |
| `tenant_id` | keyword + `is_tenant=True` | enables tenant-aware storage layout |

`is_tenant=True` on tenant_id is a Qdrant performance feature that organizes storage so all points from one tenant sit together on disk. Even though we use one collection per bot (not per tenant), the index still helps and locks in the optimization for any future migration to a shared-collection model.

---

## Acceptance criteria

Phase 3 is complete when **all** of these pass:

1. `uv run ruff check .` reports zero violations across the new files.
2. `uv run ruff format --check .` passes.
3. `uv run pytest tests/test_qdrant_client.py -v` is green (does not require Qdrant; pure unit tests).
4. `docker compose -f docker-compose.yml up -d` brings the stack up green; web is healthy on port 8080.
5. From inside the web container: `python manage.py shell -c "from apps.qdrant_core.collection import create_collection_for_bot; print(create_collection_for_bot('verifyt', 'verifyb'))"` prints `t_verifyt__b_verifyb` and the collection appears in `client.get_collections()`.
6. `docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full` exits 0 with no errors. The test collection is dropped in cleanup.
7. `docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v` is green (real-Qdrant integration tests pass).
8. `uv run pytest tests/test_qdrant_collection.py -v` from the host either runs green (if Qdrant is reachable from host via `localhost:6334`) or skips gracefully with a clear message (if not).
9. Full suite: `uv run pytest -v` keeps Phase 1's `test_healthz` and Phase 2's tests green alongside the new ones.
10. `curl -fsS http://localhost:8080/healthz | python -m json.tool` still returns the green JSON (Phase 1 + 2 regression check).

---

## Common pitfalls

1. **ColBERT size set to 128 instead of 1024.** Vanilla ColBERTv2 is 128-per-token, BGE-M3's ColBERT is 1024-per-token. Phase 4's BGE-M3 upserts will fail with dimension mismatch if this is wrong. Verify with `info.config.params.vectors["colbert"].size == 1024`.

2. **`HnswConfigDiff(m=0)` interpreted as "default m" instead of "disabled".** Verify by inspecting `info.config.params.vectors["colbert"].hnsw_config.m` — should be 0. If qdrant-client's API has changed, the right call may be a different parameter (e.g., `hnsw=None` or a separate flag).

3. **`is_tenant=True` parameter location.** This is on `KeywordIndexParams`, passed as the `field_schema` arg to `create_payload_index`. NOT on `PayloadSchemaType.KEYWORD` directly. Verify against the installed `qdrant-client` version's API.

4. **Sparse vector config without IDF modifier.** Without `Modifier.IDF`, `bm25` becomes plain term-frequency vectors — much worse retrieval quality. Schema verification must catch this drift.

5. **Concurrent `get_or_create_collection` calls race.** Two workers call exists() simultaneously, both see "missing", both call create(), one gets 409 conflict. Catch the 409 inside the helper, verify schema, return the name. Don't propagate the 409 as an error.

6. **Singleton client constructed PRE-fork.** If `get_qdrant_client()` is called at module import time (e.g., in `apps/qdrant_core/__init__.py`), gunicorn's master constructs the gRPC channel before forking. Workers inherit a broken channel. Solution: lazy construction via `lru_cache` decorator, called only on first need (handled in this spec).

7. **`@with_retry()` decorator on a method that raises non-Exception (e.g. `BaseException` like KeyboardInterrupt).** Decorator must NOT catch BaseException — only Exception. Code in spec uses `except Exception` correctly.

8. **Test collection name fails the slug regex.** Tests use `f"test_t_{uuid.hex[:8]}"` which IS slug-compliant (lowercase alphanumeric + underscore, starts with alnum). Don't accidentally use uppercase or hyphens.

9. **Tests don't drop their collections.** A failed test leaves orphan collections in Qdrant. The fixture's `try/finally` (or `yield` + teardown) MUST drop. After test runs, `client.get_collections()` should not show any `test_t_*__b_test_b_*` collections.

10. **`scripts/verify_setup.py --full` runs in CI but Qdrant isn't up.** The script must connect to Qdrant via the configured host. Inside the web container, `qdrant:6334` resolves; from the host, `localhost:6334` (per Compose port mapping). The script must read settings the same way the rest of the app does (no special test_settings overlay).

---

## Out of scope for Phase 3 (explicit)

Do **not** implement these in Phase 3. They belong to later phases:

- BGE-M3 embedder — Phase 4
- Chunker — Phase 4
- DRF serializers for upload — Phase 5
- POST `/v1/.../documents` endpoint — Phase 5
- Pipeline orchestrator (validate → lock → chunk → embed → upsert) — Phase 5
- Postgres advisory lock acquisition — Phase 5 (Phase 2 provided the helper)
- DELETE endpoint — Phase 6
- gRPC search service / `search.proto` — Phase 7
- Hybrid search query (RRF + ColBERT rerank) — Phase 7
- Quantization — v4
- Atomic version swap (`is_active` flip + grace period) — v2
- Audit log — v3

If you find yourself writing any of the above, stop and re-read this section.

---

## When you finish

1. Confirm all 10 acceptance criteria pass.
2. Commit `apps/qdrant_core/{exceptions,client,collection}.py`, the extended `scripts/verify_setup.py`, and the two test files. Verify no Phase 1 or Phase 2 file's content has been changed (other than the explicit script extension).
3. Output a short report:
   - Files created (count + paths)
   - Files modified outside Phase 3 scope (must be 0)
   - Acceptance-criteria results (✓ / ✗ per criterion)
   - Any deviations from this spec and why
   - Anything ambiguous the user should confirm before Phase 4

That's Phase 3. Phase 4 (Embedding & Chunking) builds the BGE-M3 wrapper + per-source-type chunker on top of these primitives.
