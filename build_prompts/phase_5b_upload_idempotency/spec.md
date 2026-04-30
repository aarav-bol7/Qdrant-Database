# Phase 5b — Upload Idempotency & Concurrency

> **Audience:** A coding agent building on top of verified-green Phases 1, 2, 3, 4, AND 5a at `/home/bol7/Documents/BOL7/Qdrant`. Phase 5a's `apps/ingestion/pipeline.py` and `apps/ingestion/locks.py` are the files this phase EXTENDS.

---

## Mission

Harden the upload pipeline that Phase 5a shipped:

- **content_hash short-circuit.** If `body.content_hash` matches the existing `Document.content_hash` AND `chunk_count > 0`, skip chunking/embedding/upserting. Bump `last_refreshed_at`, return 200 with `status: "no_change"`.
- **Advisory lock acquire timeout (5s).** Replace 5a's blocking `pg_advisory_lock` with `pg_try_advisory_lock` plus a short retry loop. If the lock can't be acquired in 5s, raise `ConcurrentUploadError` → HTTP 409 with `retry_after`.
- **Per-doc chunk cap (5000).** If chunking produces more than 5000 chunks for one upload, raise `DocumentTooLargeError` → 422 with `code: "too_many_chunks"`. Prevents OOM on pathological inputs.
- **`tests/test_pipeline.py`** — pipeline-level unit tests with mocked embedder. Fast (no model load).
- **`tests/test_locks.py`** — advisory lock acquire/release/timeout/concurrent acquisition tests.
- **`tests/test_upload.py` extensions** — content_hash short-circuit, concurrent uploads of same doc_id, chunk-cap rejection.

After Phase 5b: the upload endpoint handles content-identical re-uploads instantly, returns 409 instead of hanging on stuck concurrent uploads, and rejects pathological documents with a clear 422.

---

## Read first

- `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's contract; this phase EXTENDS the pipeline and locks files.
- `build_prompts/phase_5a_upload_core/implementation_report.md` — confirms 5a's deliverables, especially the PointStruct id format finding.
- `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract for the embedder.
- `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract.
- `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
- `README.md` — project charter.

If Phase 5a's deliverables don't all exist, abort: `"Phase 5a must ship green before Phase 5b can start."`

---

## Hard constraints

1. **Phase 5a is locked except** for `apps/ingestion/pipeline.py` (extended) and `apps/ingestion/locks.py` (extended). Other 5a files (serializers, views, urls, exceptions, fixtures, test_upload.py) are extended ONLY by adding new tests/fixtures, not modifying existing ones (except adding new error codes to `exceptions.py` and new error → HTTP-status mapping to `views.py`).

2. **No new dependencies.** Everything needed is already installed.

3. **content_hash short-circuit happens BEFORE any chunking/embedding/upsert/delete.** This is the entire point — the content-hash check is the cheapest first signal.

4. **content_hash short-circuit ONLY when `chunk_count > 0`.** A Document row with `chunk_count=0` indicates a partial / failed upload; treat it as fresh.

5. **content_hash must be a non-empty string** in the request body to enable the short-circuit. If absent/empty, skip the check and run the full pipeline.

6. **Advisory lock timeout: 5 seconds (locked).** Implementation: try `pg_try_advisory_lock(int4, int4)` in a loop with `time.sleep(0.05)` between attempts; total wait budget 5s. After expiry, raise `ConcurrentUploadError`.

7. **Per-doc chunk cap: 5000.** Locked. After chunking all items, count total chunks; if > 5000, raise `DocumentTooLargeError`. Don't embed; don't upsert.

8. **Maintain Phase 5a's pipeline order.** The new short-circuit happens AFTER acquiring the lock and AFTER `Tenant.objects.get_or_create` + `Bot.objects.get_or_create` (so we know a real bot exists), but BEFORE `get_or_create_collection` (no need to touch Qdrant if we're short-circuiting). Actually — simpler: short-circuit BEFORE collection creation too. If the doc exists and content matches, the collection must already exist (since the chunks are there). Skip everything Qdrant-side.

9. **Order of new checks (locked):**
   ```
   ... 5a's lock acquire ...
   1. Tenant.get_or_create (5a)
   2. Bot.get_or_create (5a)
   3. Look up existing Document
   4. NEW: if existing AND content_hash matches AND chunk_count > 0:
        existing.last_refreshed_at = now()
        existing.save()
        return UploadResult(status="no_change", chunks_created=existing.chunk_count, ...)
   5. get_or_create_collection (5a)
   6. Chunk every item.content (5a)
   7. NEW: if total chunks > 5000:
        raise DocumentTooLargeError
   8. ... rest of 5a's pipeline ...
   ```

10. **`tests/test_pipeline.py` mocks the embedder.** Use `unittest.mock.patch` on `apps.ingestion.pipeline.embed_passages`. Fast: <1s per test.

11. **`tests/test_locks.py` tests against real Postgres** via Compose. Skip-not-fail if Postgres unreachable.

12. **No code comments unless spec or invariant justifies.** No emoji. No `*.md` beyond `implementation_report.md`.

---

## API contract additions

**Response codes added in 5b:**

| Code | When | Body |
|------|------|------|
| 200 | content_hash matches existing doc | `{ doc_id, status: "no_change", chunks_created: <existing.chunk_count>, items_processed: <existing.item_count>, collection_name }` |
| 409 | Advisory lock acquire timed out (5s) — another worker is uploading the same doc_id | `{ error: { code: "concurrent_upload", message, retry_after: <seconds> } }` |
| 422 | New: chunk count exceeds 5000 (DocumentTooLargeError) | `{ error: { code: "too_many_chunks", message, details: { chunk_count, max: 5000 } } }` |

5a's other codes (201, 400, 422 for empty content, 500) are unchanged.

---

## Deliverables

```
qdrant_rag/
├── apps/ingestion/
│   ├── pipeline.py            ← MODIFY (add short-circuit + chunk cap)
│   └── locks.py               ← MODIFY (add timeout)
├── apps/documents/
│   ├── exceptions.py          ← EXTEND (add ConcurrentUploadError, DocumentTooLargeError)
│   └── views.py               ← MINOR EXTEND (map new errors to status codes; add `retry_after` header on 409)
└── tests/
    ├── test_pipeline.py       ← NEW
    ├── test_locks.py          ← NEW
    ├── test_upload.py         ← EXTEND (3 new tests)
    └── fixtures/
        └── (no new fixtures needed — reuse 5a's)
```

5 changed files, no new fixtures.

---

## File-by-file specification

### `apps/documents/exceptions.py` (EXTEND)

Add to the existing file from 5a:

```python
class ConcurrentUploadError(UploadError):
    http_status = 409
    code = "concurrent_upload"

    def __init__(self, message: str, retry_after: int = 5, details: dict | None = None) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class DocumentTooLargeError(UploadError):
    http_status = 422
    code = "too_many_chunks"
```

### `apps/ingestion/locks.py` (MODIFY — add try-acquire with timeout)

Replace 5a's blocking acquire with a try-with-timeout pattern:

```python
"""Postgres advisory lock context manager with acquire timeout."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Generator

from django.db import connection

from apps.documents.exceptions import ConcurrentUploadError
from apps.qdrant_core.naming import advisory_lock_key

logger = logging.getLogger(__name__)

DEFAULT_ACQUIRE_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05


@contextlib.contextmanager
def upload_lock(
    tenant_id: str,
    bot_id: str,
    doc_id: str,
    *,
    timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT_S,
) -> Generator[None]:
    """Try to acquire pg_advisory_lock; raise ConcurrentUploadError on timeout."""
    key1, key2 = advisory_lock_key(tenant_id, bot_id, doc_id)
    deadline = time.monotonic() + timeout_s
    acquired = False
    with connection.cursor() as cursor:
        while True:
            cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [key1, key2])
            (got,) = cursor.fetchone()
            if got:
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise ConcurrentUploadError(
                    f"Could not acquire lock for {tenant_id}/{bot_id}/{doc_id} within {timeout_s}s",
                    retry_after=int(timeout_s),
                    details={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
                )
            time.sleep(_POLL_INTERVAL_S)

        try:
            logger.debug(
                "advisory_lock_acquired",
                extra={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
            )
            yield
        finally:
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key1, key2])
                logger.debug(
                    "advisory_lock_released",
                    extra={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
                )
```

### `apps/ingestion/pipeline.py` (MODIFY — add short-circuit + chunk cap)

Insert into Phase 5a's pipeline:

**Before** the call to `chunk_item` for each item:

```python
# After Tenant + Bot get_or_create, before collection get_or_create:
existing = Document.objects.filter(doc_id=doc_id).first()
is_replace = existing is not None

# NEW: content_hash short-circuit
incoming_hash = (body.get("content_hash") or "").strip()
if (
    existing
    and existing.chunk_count > 0
    and incoming_hash
    and existing.content_hash == incoming_hash
):
    if existing.tenant_id != tenant_id or existing.bot_id != bot_id:
        raise QdrantWriteError(
            "doc_id collision across tenants/bots — refuse to short-circuit.",
            details={"doc_id": doc_id},
        )
    existing.save(update_fields=["last_refreshed_at"])  # auto_now updates the timestamp
    logger.info(
        "upload_no_change",
        extra={
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "doc_id": doc_id,
            "chunk_count": existing.chunk_count,
        },
    )
    return UploadResult(
        doc_id=doc_id,
        chunks_created=existing.chunk_count,
        items_processed=existing.item_count,
        collection_name=collection_name(tenant_id, bot_id),
        status="no_change",
    )
```

Note: `collection_name(tenant_id, bot_id)` is imported from `apps.qdrant_core.naming` (no Qdrant call needed for the no_change path).

**After** chunking all items:

```python
# Phase 5b: per-doc chunk cap
MAX_CHUNKS_PER_DOC = 5000
if len(flat) > MAX_CHUNKS_PER_DOC:
    raise DocumentTooLargeError(
        f"Document produces {len(flat)} chunks, max is {MAX_CHUNKS_PER_DOC}",
        details={"chunk_count": len(flat), "max": MAX_CHUNKS_PER_DOC},
    )
```

**Update the response shape** for `status: "no_change"`:

```python
return UploadResult(
    doc_id=doc_id,
    chunks_created=existing.chunk_count,
    items_processed=existing.item_count,
    collection_name=collection_name(tenant_id, bot_id),
    status="no_change",
)
```

The view should detect `status == "no_change"` and return HTTP 200 instead of 201.

### `apps/documents/views.py` (MINOR EXTEND)

Update the success response handling:

```python
status_code = 200 if result.status == "no_change" else 201
return Response({...}, status=status_code)
```

Update the error handling so `ConcurrentUploadError`'s `retry_after` is added to the response body and as a `Retry-After` header:

```python
except ConcurrentUploadError as exc:
    response = _error_response(
        http_status=exc.http_status,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )
    response["Retry-After"] = str(exc.retry_after)
    return response
```

### `tests/test_pipeline.py` (NEW)

Pipeline-level tests with mocked embedder. Fast.

```python
from unittest.mock import patch

import numpy as np
import pytest

from apps.documents.exceptions import (
    ConcurrentUploadError,
    DocumentTooLargeError,
    NoEmbeddableContentError,
)
from apps.documents.models import Document
from apps.ingestion.pipeline import UploadPipeline
from apps.tenants.models import Bot, Tenant


def _mock_embeddings(n: int) -> dict:
    return {
        "dense": [np.zeros(1024, dtype=np.float32) for _ in range(n)],
        "sparse": [{"42": 0.5} for _ in range(n)],
        "colbert": [np.zeros((3, 1024), dtype=np.float32) for _ in range(n)],
    }


@pytest.fixture
def mock_embedder():
    with (
        patch("apps.ingestion.pipeline.embed_passages") as embed_mock,
        patch("apps.ingestion.pipeline.get_qdrant_client") as client_mock,
        patch("apps.ingestion.pipeline.get_or_create_collection") as goc_mock,
        patch("apps.ingestion.pipeline.delete_by_doc_id") as del_mock,
    ):
        embed_mock.side_effect = lambda texts: _mock_embeddings(len(texts))
        goc_mock.return_value = "t_test_t__b_test_b"
        del_mock.return_value = 0
        yield {
            "embed": embed_mock,
            "client": client_mock.return_value,
            "goc": goc_mock,
            "del": del_mock,
        }


def _body(items_count: int = 1, content_hash: str = "sha256:abc") -> dict:
    return {
        "source_type": "pdf",
        "source_filename": "x.pdf",
        "content_hash": content_hash,
        "items": [
            {"item_index": i, "content": f"Some content for item {i}." * 30}
            for i in range(items_count)
        ],
        "custom_metadata": {},
    }


@pytest.mark.django_db
class TestContentHashShortCircuit:
    def test_no_change_when_content_hash_matches_and_chunks_exist(self, mock_embedder):
        body = _body(items_count=1)
        # First upload — runs full pipeline
        r1 = UploadPipeline.execute(
            tenant_id="test_t", bot_id="test_b", doc_id="doc-1", body=body
        )
        assert r1.status == "created"
        # Second with same content_hash — short-circuit
        r2 = UploadPipeline.execute(
            tenant_id="test_t", bot_id="test_b", doc_id="doc-1", body=body
        )
        assert r2.status == "no_change"
        # Embed not called the second time
        assert mock_embedder["embed"].call_count == 1

    def test_full_pipeline_when_content_hash_differs(self, mock_embedder):
        body1 = _body(content_hash="sha256:aaa")
        UploadPipeline.execute(
            tenant_id="test_t", bot_id="test_b", doc_id="doc-1", body=body1
        )
        body2 = _body(content_hash="sha256:bbb")
        r = UploadPipeline.execute(
            tenant_id="test_t", bot_id="test_b", doc_id="doc-1", body=body2
        )
        assert r.status == "replaced"
        # Embedded twice (once per upload)
        assert mock_embedder["embed"].call_count == 2

    def test_full_pipeline_when_content_hash_absent(self, mock_embedder):
        body1 = _body(content_hash="")
        UploadPipeline.execute(
            tenant_id="test_t", bot_id="test_b", doc_id="doc-1", body=body1
        )
        body2 = _body(content_hash="")
        UploadPipeline.execute(
            tenant_id="test_t", bot_id="test_b", doc_id="doc-1", body=body2
        )
        # Both runs hit the embedder
        assert mock_embedder["embed"].call_count == 2


@pytest.mark.django_db
class TestChunkCap:
    def test_too_many_chunks_raises_document_too_large(self, mock_embedder):
        # 5001 items each producing 1 chunk → total > MAX_CHUNKS_PER_DOC
        body = {
            "source_type": "faq",  # smaller chunks
            "items": [
                {"item_index": i, "content": "Q? A."}
                for i in range(5001)
            ],
        }
        with pytest.raises(DocumentTooLargeError):
            UploadPipeline.execute(
                tenant_id="test_t", bot_id="test_b", doc_id="doc-big", body=body
            )
        # No upsert attempted
        assert mock_embedder["client"].upsert.call_count == 0


@pytest.mark.django_db
class TestNoEmbeddableContent:
    def test_all_empty_content_raises(self, mock_embedder):
        body = {
            "source_type": "pdf",
            "items": [
                {"item_index": 0, "content": "   "},  # whitespace only
            ],
        }
        with pytest.raises(NoEmbeddableContentError):
            UploadPipeline.execute(
                tenant_id="test_t", bot_id="test_b", doc_id="doc-empty", body=body
            )
```

### `tests/test_locks.py` (NEW)

Real-Postgres lock tests via Compose. Skip if unreachable.

```python
import threading
import time

import pytest

from apps.documents.exceptions import ConcurrentUploadError
from apps.ingestion.locks import upload_lock


@pytest.mark.django_db(transaction=True)
class TestAdvisoryLock:
    def test_acquire_and_release(self):
        with upload_lock("test_t", "test_b", "doc-1"):
            pass

    def test_concurrent_acquire_blocks(self):
        """Worker A holds the lock; Worker B times out after 1s."""
        from django.db import connections

        result = {}

        def worker_b():
            try:
                with upload_lock("test_t", "test_b", "doc-1", timeout_s=1.0):
                    result["b"] = "acquired"
            except ConcurrentUploadError:
                result["b"] = "timeout"
            finally:
                connections.close_all()

        with upload_lock("test_t", "test_b", "doc-1"):
            t = threading.Thread(target=worker_b)
            t.start()
            t.join(timeout=3.0)

        assert result.get("b") == "timeout"

    def test_different_keys_dont_block(self):
        """Locks for different doc_ids don't collide."""
        from django.db import connections

        result = {}

        def worker_b():
            try:
                with upload_lock("test_t", "test_b", "doc-different", timeout_s=1.0):
                    result["b"] = "acquired"
            except ConcurrentUploadError:
                result["b"] = "timeout"
            finally:
                connections.close_all()

        with upload_lock("test_t", "test_b", "doc-1"):
            t = threading.Thread(target=worker_b)
            t.start()
            t.join(timeout=3.0)

        assert result.get("b") == "acquired"
```

### `tests/test_upload.py` (EXTEND)

Add to the existing file from 5a:

```python
@pytest.mark.django_db
def test_200_content_hash_short_circuit(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    body["content_hash"] = "sha256:matching"
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"

    r1 = client.post(url, body, format="json")
    assert r1.status_code == 201

    r2 = client.post(url, body, format="json")
    assert r2.status_code == 200
    assert r2.json()["status"] == "no_change"


@pytest.mark.django_db
def test_422_too_many_chunks(client, fresh_bot):
    tenant, bot = fresh_bot
    # FAQ items (200 tokens / 10% overlap) — 1 chunk each.
    # 5001 items → 5001 chunks → cap exceeded.
    body = {
        "source_type": "faq",
        "items": [
            {"item_index": i, "content": "Question? Answer text here."}
            for i in range(5001)
        ],
    }
    response = client.post(
        f"/v1/tenants/{tenant}/bots/{bot}/documents",
        body,
        format="json",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_many_chunks"
```

---

## Acceptance criteria

Phase 5b is complete when **all** of these pass:

1. `uv run ruff check .` — zero violations.
2. `uv run ruff format --check .` — zero changes.
3. `uv run pytest tests/test_pipeline.py -v` — green (with mocked embedder, no real model).
4. `uv run pytest tests/test_locks.py -v` — green (against running Postgres, skip if unreachable).
5. `docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v` — green (full Phase 5a + 5b suite).
6. Manual smoke (with stack up):
   ```bash
   DOC_ID=$(uuidgen)
   sed "s/^{/{\"doc_id\":\"$DOC_ID\",\"content_hash\":\"sha256:fixed\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json
   curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\n%{http_code}\n"  # 201
   curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\n%{http_code}\n"  # 200 no_change
   ```
7. Phase 5a's tests still green: `docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v`.
8. Phase 1+2+3+4+5a regression: `uv run pytest -v` (host) + `make health`.
9. `git status --short` shows ONLY: `apps/ingestion/{pipeline,locks}.py`, `apps/documents/{exceptions,views}.py`, `tests/test_{pipeline,locks,upload}.py`, `build_prompts/phase_5b_upload_idempotency/implementation_report.md`.
10. `make health` returns green JSON.

---

## Common pitfalls

1. **Short-circuit before lock release.** The Document.save() updates `last_refreshed_at`; this happens INSIDE the upload_lock context. Don't accidentally exit the context manager before saving.

2. **`pg_try_advisory_lock` returns `false` instead of blocking.** Confirm with a quick test: two simultaneous calls — one returns `true`, the other returns `false`. If the second blocks, you're using the wrong function.

3. **`Connection close_all()` in test fixture.** The `test_concurrent_acquire_blocks` test uses a separate thread which has its own DB connection. Without `connections.close_all()` in the worker's finally, that connection holds the lock until process exit, breaking subsequent tests.

4. **Existing tests in `test_upload.py` start failing.** When extending the file, append to it; don't rewrite. Verify by running `pytest tests/test_upload.py -v` and confirming all 5a tests still appear in the output and pass.

5. **`update_fields=["last_refreshed_at"]` doesn't trigger `auto_now`.** Django's `auto_now=True` updates on `.save()` regardless of `update_fields` — but explicitly include the field for clarity. Verify with a unit test.

6. **`existing.tenant_id != tenant_id` check in short-circuit.** If a doc_id collision happens across tenants (astronomical, but UUIDs are not perfect), the short-circuit must NOT silently update someone else's data. Raise QdrantWriteError → 500.

7. **5001-item chunk cap test runs slow.** Each item is chunked individually. For `source_type=faq` (200 tokens / chunk), 5001 short items → 5001 chunks. Each goes through chunker — that's many tokenizer calls. Test runtime might be ~10s. Acceptable; use `@pytest.mark.slow` if you want to skip in CI fast iteration.

8. **`MAX_CHUNKS_PER_DOC = 5000` constant location.** Spec puts it in `pipeline.py`. Keep it there (not in `chunker.py`) — it's a pipeline-level policy, not a chunker concern.

9. **`ConcurrentUploadError` carries `retry_after`.** The view must read it and add the `Retry-After` HTTP header. Test the header presence, not just the response body.

10. **Pipeline tests under `@pytest.mark.django_db` may run slow.** Each test creates Tenant/Bot/Document via `get_or_create` and rolls back the transaction at end. Default pytest-django transaction-per-test is fine.

---

## Out of scope for Phase 5b

- DELETE endpoint — Phase 6
- Atomic version swap (is_active flip + grace period) — v2
- gRPC search — Phase 7
- Audit log — v3

If you find yourself writing any of these, stop.

---

## When you finish

1. Confirm all 10 acceptance criteria pass.
2. Commit:
   - `apps/ingestion/pipeline.py` (modified)
   - `apps/ingestion/locks.py` (modified)
   - `apps/documents/exceptions.py` (extended)
   - `apps/documents/views.py` (minor extend)
   - `tests/test_pipeline.py` (new)
   - `tests/test_locks.py` (new)
   - `tests/test_upload.py` (extended)
   - `build_prompts/phase_5b_upload_idempotency/implementation_report.md`
3. Verify NO Phase 1/2/3/4 file modified, AND no Phase 5a file outside the explicitly-extended ones.
4. Output a short report.

That's Phase 5b. After this ships green, **the upload feature is complete**. Phase 6 builds the corresponding DELETE endpoint.
