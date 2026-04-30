# Phase 6 — Delete API

> **Audience:** A coding agent building on top of verified-green Phases 1, 2, 3, 4, 5a, 5b at `/home/bol7/Documents/BOL7/Qdrant`. Phase 5's Upload API already routes `/v1/...` and exposes the helpers Phase 6 reuses.

---

## Mission

Add the DELETE endpoint that completes the document lifecycle:

- **`DELETE /v1/tenants/<tenant_id>/bots/<bot_id>/documents/<doc_id>`** — soft-deletes the Document row in Postgres (`status="deleted"`, `chunk_count=0`) and hard-deletes the chunks from Qdrant via Phase 3's `delete_by_doc_id`. Idempotent: deleting an already-deleted Document still returns 204; deleting a never-uploaded `doc_id` returns 404.
- Reuses Phase 5b's `upload_lock` (5s timeout) for concurrency safety. Same lock key as upload, so DELETE-during-UPLOAD serializes correctly.
- Adds `DocumentNotFoundError` to `apps/documents/exceptions.py`. Reuses `ConcurrentUploadError` for delete-side lock contention (renaming is a Phase 8 polish item).

After Phase 6: the upload + delete API surface is **complete**. DynamicADK can drive the full document lifecycle. Phase 7 adds the gRPC search service.

---

## Read first

- `build_prompts/phase_5b_upload_idempotency/spec.md` — Phase 5b's `upload_lock(tenant_id, bot_id, doc_id, timeout_s=5.0)` is what Phase 6 reuses.
- `build_prompts/phase_5b_upload_idempotency/implementation_report.md` — Phase 5b outcomes.
- `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's view + serializer + URL routing patterns.
- `build_prompts/phase_5a_upload_core/implementation_report.md` — Phase 5a outcomes; note the PointStruct id format choice if relevant.
- `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3's `delete_by_doc_id` is the Qdrant-side helper.
- `build_prompts/phase_2_domain_models/spec.md` — `Document.status` field choices include `"deleted"`. `slug_validator` for URL params.
- `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.
- `README.md` — context.

---

## Hard constraints

1. **Phase 1/2/3/4/5a/5b are locked.** Phase 6 EXTENDS:
   - `apps/documents/urls.py` (add the DELETE route)
   - `apps/documents/views.py` (add `DeleteDocumentView`)
   - `apps/documents/exceptions.py` (add `DocumentNotFoundError`)
   - `apps/ingestion/pipeline.py` (add `DeletePipeline` class)
   No other Phase 1-5 file modified.

2. **No new dependencies.** Everything needed is installed.

3. **URL pattern:** `DELETE /v1/tenants/<str:tenant_id>/bots/<str:bot_id>/documents/<uuid:doc_id>`. The `<uuid:>` URL converter validates the UUID format at dispatch — malformed UUIDs return Django's default 404 page (acceptable; it's programmer error, not real client misuse).

4. **Slug validation on `tenant_id` and `bot_id`** via Phase 2's `validate_slug`. On failure: 400 with `{"error": {"code": "invalid_slug", ...}}`.

5. **Use Phase 5b's `upload_lock` directly** (not a separate `delete_lock`). Same key — the DELETE serializes against any in-flight UPLOAD for the same doc_id.

6. **Soft delete in Postgres + hard delete in Qdrant.**
   - Document row STAYS in Postgres with `status="deleted"`, `chunk_count=0`, `error_message=None`. The row is preserved for v3's audit log.
   - Qdrant chunks are HARD-deleted via Phase 3's `delete_by_doc_id`.

7. **Pipeline order (locked):**
   ```
   1. Validate URL slugs (Phase 2's validate_slug)
   2. <uuid:doc_id> already validated by Django URL dispatch
   3. Acquire upload_lock(tenant_id, bot_id, doc_id, timeout_s=5.0)
   4. Look up existing Document by doc_id
   5a. If Document doesn't exist OR is in a different tenant/bot:
       → raise DocumentNotFoundError → 404
   5b. If Document exists and matches:
       → call delete_by_doc_id (idempotent; returns 0 if already gone)
       → update Document.status="deleted", chunk_count=0
   6. Release lock; return 204 (no body)
   ```

8. **Idempotent re-delete returns 204.** Deleting a doc that's already `status="deleted"` is a no-op + same 204 response. The operation is "ensure absent."

9. **Cross-tenant doc_id collision returns 404.** If `Document.objects.filter(doc_id=...).first()` returns a row whose `tenant_id`/`bot_id` doesn't match the URL params, return 404 (do not leak existence). Do NOT return 500.

10. **204 No Content has NO response body** — RFC-compliant. Log the chunks_deleted count at INFO instead.

11. **Reuse `ConcurrentUploadError` for delete-side contention.** Don't add a new `ConcurrentDeleteError` class. The HTTP code (409) and the existing `Retry-After` header behavior are correct.

12. **No code comments unless spec or invariant justifies. No emoji. No `*.md` beyond `implementation_report.md`.**

---

## API contract

**URL:** `DELETE /v1/tenants/<tenant_id>/bots/<bot_id>/documents/<doc_id>`
**Auth:** None (DRF `AllowAny`).
**Request body:** None (DELETE has no body).

**Response codes:**

| Code | When | Body |
|------|------|------|
| 204 | Document found and deleted (or already deleted; idempotent) | (none) |
| 400 | `tenant_id` or `bot_id` fails slug regex | `{ error: { code: "invalid_slug", message } }` |
| 404 | Malformed `doc_id` (Django default 404 page) OR Document not found in Postgres | for valid-UUID-but-no-doc: `{ error: { code: "document_not_found", message } }` |
| 409 | Lock contention (concurrent upload/delete on same doc_id) | `{ error: { code: "concurrent_upload", message, retry_after } }` + `Retry-After` HTTP header |
| 500 | Qdrant unreachable after retries, Postgres failure | `{ error: { code, message } }` |

---

## Deliverables

```
qdrant_rag/
├── apps/documents/
│   ├── urls.py                ← EXTEND (add DELETE route)
│   ├── views.py               ← EXTEND (add DeleteDocumentView)
│   └── exceptions.py          ← EXTEND (add DocumentNotFoundError)
├── apps/ingestion/
│   └── pipeline.py            ← EXTEND (add DeletePipeline class)
└── tests/
    └── test_delete.py         ← NEW
```

4 modified + 1 new = 5 changed files.

---

## File-by-file specification

### `apps/documents/exceptions.py` (EXTEND)

Add:

```python
class DocumentNotFoundError(UploadError):
    """Document with the given doc_id doesn't exist in this tenant/bot."""

    http_status = 404
    code = "document_not_found"
```

Don't modify any existing class.

### `apps/ingestion/pipeline.py` (EXTEND)

Add a new class alongside `UploadPipeline`:

```python
@dataclass(frozen=True)
class DeleteResult:
    doc_id: str
    chunks_deleted: int
    was_already_deleted: bool


class DeletePipeline:
    @staticmethod
    def execute(*, tenant_id: str, bot_id: str, doc_id: str) -> DeleteResult:
        started = time.monotonic()
        with upload_lock(tenant_id, bot_id, doc_id):
            existing = Document.objects.filter(doc_id=doc_id).first()
            if not existing:
                raise DocumentNotFoundError(
                    f"Document {doc_id} not found.",
                    details={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
                )
            if existing.tenant_id != tenant_id or existing.bot_id != bot_id:
                # Cross-tenant doc_id collision — DO NOT leak existence.
                raise DocumentNotFoundError(
                    f"Document {doc_id} not found.",
                    details={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
                )

            was_already_deleted = existing.status == Document.DELETED

            try:
                chunks_deleted = delete_by_doc_id(tenant_id, bot_id, doc_id)
            except QdrantError as exc:
                raise QdrantWriteError(
                    f"delete_by_doc_id failed: {exc}",
                    details={"doc_id": doc_id},
                ) from exc

            existing.status = Document.DELETED
            existing.chunk_count = 0
            existing.error_message = None
            existing.save(
                update_fields=["status", "chunk_count", "error_message", "last_refreshed_at"]
            )

            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "delete_succeeded",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id,
                    "chunks_deleted": chunks_deleted,
                    "was_already_deleted": was_already_deleted,
                    "elapsed_ms": elapsed_ms,
                },
            )

            return DeleteResult(
                doc_id=doc_id,
                chunks_deleted=chunks_deleted,
                was_already_deleted=was_already_deleted,
            )
```

The imports needed at the top of pipeline.py (some already exist from Phase 5):

```python
# Existing imports stay; add only the missing one:
from apps.documents.exceptions import (
    EmbedderError,
    NoEmbeddableContentError,
    QdrantWriteError,
    DocumentNotFoundError,        # NEW
    DocumentTooLargeError,         # from Phase 5b
)
```

### `apps/documents/views.py` (EXTEND)

Add a new view class alongside `UploadDocumentView`:

```python
class DeleteDocumentView(APIView):
    permission_classes = [permissions.AllowAny]

    def delete(self, request: Request, tenant_id: str, bot_id: str, doc_id) -> Response:
        # 1. Validate URL slugs
        try:
            validate_slug(tenant_id, field_name="tenant_id")
            validate_slug(bot_id, field_name="bot_id")
        except InvalidIdentifierError as exc:
            return _error_response(
                http_status=400,
                code="invalid_slug",
                message=str(exc),
            )

        # doc_id is already validated as UUID by the URL converter.
        doc_id_str = str(doc_id)

        # 2. Run the delete pipeline.
        try:
            result = DeletePipeline.execute(
                tenant_id=tenant_id,
                bot_id=bot_id,
                doc_id=doc_id_str,
            )
        except ConcurrentUploadError as exc:
            response = _error_response(
                http_status=exc.http_status,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
            response["Retry-After"] = str(exc.retry_after)
            return response
        except UploadError as exc:
            logger.error(
                "delete_failed",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id_str,
                    "code": exc.code,
                },
                exc_info=True,
            )
            return _error_response(
                http_status=exc.http_status,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )

        return Response(status=204)
```

Imports to add at the top of views.py (some already exist):

```python
from apps.documents.exceptions import (
    ConcurrentUploadError,        # already exists from Phase 5b
    InvalidPayloadError,
    UploadError,
)
from apps.ingestion.pipeline import (
    DeletePipeline,
    UploadPipeline,
    UploadResult,
)
```

### `apps/documents/urls.py` (EXTEND)

Add the DELETE route alongside the existing POST route:

```python
from django.urls import path

from apps.documents.views import DeleteDocumentView, UploadDocumentView

urlpatterns = [
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/documents",
        UploadDocumentView.as_view(),
        name="upload-document",
    ),
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/documents/<uuid:doc_id>",
        DeleteDocumentView.as_view(),
        name="delete-document",
    ),
]
```

### `tests/test_delete.py` (NEW)

Integration tests via DRF's `APIClient`. Reuses Phase 5a's fixtures.

```python
import json
import pathlib
import uuid

import pytest
from rest_framework.test import APIClient

from apps.documents.models import Document

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def qdrant_available():
    try:
        from apps.qdrant_core.client import get_qdrant_client
        get_qdrant_client.cache_clear()
        get_qdrant_client().get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant unreachable: {exc}")


@pytest.fixture
def fresh_bot(qdrant_available):
    tenant = f"test_t_{uuid.uuid4().hex[:8]}"
    bot = f"test_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    try:
        from apps.qdrant_core.collection import drop_collection
        drop_collection(tenant, bot)
    except Exception:
        pass


@pytest.fixture
def client():
    return APIClient()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def uploaded_doc(client, fresh_bot):
    """Upload a doc and return (tenant, bot, doc_id) for use in delete tests."""
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    r = client.post(url, body, format="json")
    assert r.status_code == 201, r.json()
    return tenant, bot, body["doc_id"]


@pytest.mark.django_db
def test_204_delete_existing_doc(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc
    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}")
    assert response.status_code == 204
    assert response.content == b""  # 204 has no body


@pytest.mark.django_db
def test_204_idempotent_redelete(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}"
    r1 = client.delete(url)
    assert r1.status_code == 204
    r2 = client.delete(url)
    assert r2.status_code == 204


@pytest.mark.django_db
def test_404_nonexistent_doc(client, fresh_bot):
    tenant, bot = fresh_bot
    random_uuid = str(uuid.uuid4())
    response = client.delete(
        f"/v1/tenants/{tenant}/bots/{bot}/documents/{random_uuid}"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


@pytest.mark.django_db
def test_400_invalid_tenant_slug(client):
    random_uuid = str(uuid.uuid4())
    response = client.delete(
        f"/v1/tenants/Pizza-Palace/bots/sup/documents/{random_uuid}"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_slug"


@pytest.mark.django_db
def test_400_invalid_bot_slug(client):
    random_uuid = str(uuid.uuid4())
    response = client.delete(
        f"/v1/tenants/pizzapalace/bots/Bad-Bot/documents/{random_uuid}"
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_404_malformed_uuid_returns_django_404(client, fresh_bot):
    tenant, bot = fresh_bot
    response = client.delete(
        f"/v1/tenants/{tenant}/bots/{bot}/documents/not-a-uuid"
    )
    # Django's <uuid:> converter rejects this BEFORE the view runs.
    # Default 404 (HTML or plain), not our envelope.
    assert response.status_code == 404


@pytest.mark.django_db
def test_document_soft_deleted_in_postgres(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc
    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}")
    assert response.status_code == 204

    doc = Document.objects.get(doc_id=doc_id)
    assert doc.status == Document.DELETED
    assert doc.chunk_count == 0


@pytest.mark.django_db
def test_qdrant_chunks_gone_after_delete(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc

    from apps.qdrant_core.client import get_qdrant_client
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    qclient = get_qdrant_client()
    name = f"t_{tenant}__b_{bot}"
    before = qclient.count(
        name,
        count_filter=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
        exact=True,
    ).count
    assert before > 0

    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}")
    assert response.status_code == 204

    after = qclient.count(
        name,
        count_filter=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
        exact=True,
    ).count
    assert after == 0


@pytest.mark.django_db
def test_404_cross_tenant_doc_id(client, fresh_bot):
    """If a doc_id exists in tenant A, deleting from tenant B returns 404."""
    tenant_a, bot_a = fresh_bot
    tenant_b = f"test_t2_{uuid.uuid4().hex[:8]}"
    bot_b = f"test_b2_{uuid.uuid4().hex[:8]}"

    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    r1 = client.post(f"/v1/tenants/{tenant_a}/bots/{bot_a}/documents", body, format="json")
    assert r1.status_code == 201

    # Try to delete the same doc_id from a different tenant/bot.
    response = client.delete(
        f"/v1/tenants/{tenant_b}/bots/{bot_b}/documents/{body['doc_id']}"
    )
    assert response.status_code == 404
    # Cleanup tenant_b's collection if it was auto-created.
    try:
        from apps.qdrant_core.collection import drop_collection
        drop_collection(tenant_b, bot_b)
    except Exception:
        pass
```

---

## Acceptance criteria

Phase 6 is complete when **all** of these pass:

1. `uv run ruff check .` — zero violations.
2. `uv run ruff format --check .` — zero changes.
3. `uv run python manage.py check` — exits 0.
4. `uv run python manage.py makemigrations --check --dry-run` — no pending migrations.
5. Stack rebuild: `make down && make up && sleep 90 && make health` — green JSON.
6. From host with stack up:
   ```bash
   # Upload a doc
   DOC_ID=$(uuidgen)
   sed "s/^{/{\"doc_id\":\"$DOC_ID\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json
   curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
        -H "Content-Type: application/json" \
        -d @/tmp/with-id.json -w "\n%{http_code}\n"   # 201

   # Delete it
   curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\n%{http_code}\n"   # 204

   # Re-delete (idempotent)
   curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\n%{http_code}\n"   # 204

   # Delete random non-existent uuid
   curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$(uuidgen) -w "\n%{http_code}\n"   # 404

   # Bad slug
   curl -sS -X DELETE http://localhost:8080/v1/tenants/Bad-Slug/bots/sup/documents/$(uuidgen) -w "\n%{http_code}\n"   # 400
   ```
7. `docker compose -f docker-compose.yml exec web pytest tests/test_delete.py -v` — green.
8. `uv run pytest -v` (host) — keeps all prior phase tests green; embedder-loading tests skip gracefully if model not on host.
9. `git status --short` shows ONLY the 4 modified files + 1 new test file + the implementation report.
10. Phase 1+2+3+4+5a+5b regression: `make health` still 200; existing `tests/test_upload.py`, `test_pipeline.py`, `test_locks.py`, `test_models.py`, `test_naming.py`, `test_qdrant_*.py`, `test_chunker.py`, `test_payload.py`, `test_embedder.py`, `test_healthz.py` all still green.

---

## Common pitfalls

1. **Forgetting `<uuid:doc_id>` in the URL pattern.** If you write `<str:doc_id>`, Django passes a string and the view must validate it manually. The spec uses `<uuid:>` for built-in validation.

2. **Using `Document.objects.filter(doc_id=doc_id, tenant_id=tenant_id, bot_id=bot_id).first()`.** This is fine but masks the cross-tenant collision case as a clean 404. The spec explicitly fetches by `doc_id` only and verifies tenant/bot match — same outcome (404), but the *log message* clearly indicates whether it was "missing" or "cross-tenant collision."

3. **204 with body.** DRF `Response(status=204)` returns no body by default. Don't pass a `data=` kwarg; it'd be ignored but still wasteful. `response.content == b""`.

4. **Locking on the wrong key.** Use `(tenant_id, bot_id, doc_id)` — same as upload — so DELETE-during-UPLOAD serializes correctly. Different lock per operation type would be a race condition.

5. **`Document.save(update_fields=[...])` missing fields.** Spec lists `["status", "chunk_count", "error_message", "last_refreshed_at"]`. Forgetting `last_refreshed_at` means the audit timestamp doesn't update.

6. **`chunks_deleted` count from `delete_by_doc_id`.** Phase 3's helper returns the actual deleted count. Use it in the log line — don't hard-code `0`.

7. **Reusing `ConcurrentUploadError` for delete contention.** This works (HTTP 409 + Retry-After), but the error code string in the response is `concurrent_upload`. v1 acceptable; clients shouldn't case on the string.

8. **Tests require warm embedder for the upload step.** `uploaded_doc` fixture posts a real doc, which triggers BGE-M3 load. Run inside the container or after `verify_setup.py --full` warmup.

9. **`test_404_malformed_uuid_returns_django_404`** asserts only that status is 404 — Django's default 404 page is HTML (or plain text), not our JSON envelope. Don't assert on response.json().

10. **`test_qdrant_chunks_gone_after_delete`** queries Qdrant directly. If the collection was auto-created with the wrong schema (Phase 3's drift detection), the count call may fail. Fixture cleanup uses `drop_collection` to avoid pollution.

---

## Out of scope for Phase 6

- gRPC search service — Phase 7
- Atomic version swap (`is_active` flip + grace period) — v2
- Audit log table — v3
- Hard-deleting the Document row — v3 (audit log preserves the soft-deleted record)
- Bulk delete (delete-all-docs-for-bot) — v5 with explicit CRUD endpoints
- Async deletion via Celery — v2
- Renaming `ConcurrentUploadError` → `ConcurrentOperationError` — Phase 8 polish

If you find yourself writing any of these, stop.

---

## When you finish

1. Confirm all 10 acceptance criteria pass.
2. Commit:
   - `apps/documents/urls.py` (extended)
   - `apps/documents/views.py` (extended)
   - `apps/documents/exceptions.py` (extended)
   - `apps/ingestion/pipeline.py` (extended)
   - `tests/test_delete.py` (new)
   - `build_prompts/phase_6_delete_api/implementation_report.md`
3. Verify NO Phase 1-5 file modified outside the 4 explicitly-extended ones.
4. Output a short report.

That's Phase 6. After this ships green, **the document lifecycle (upload + delete) is complete.** Phase 7 adds the gRPC search service.
