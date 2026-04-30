# Phase 5a — Upload Pipeline (Core)

> **Audience:** A coding agent (e.g. Claude Code) building on top of verified-green Phase 1, 2, 3, and 4 at `/home/bol7/Documents/BOL7/Qdrant`. Do NOT modify Phase 1/2/3/4 deliverables except where this spec explicitly says so.

---

## Mission

Build the working end-to-end upload endpoint:

- **DRF serializer** at `apps/documents/serializers.py` — validates the trimmed Section-9 upload payload (source_type, source_filename, source_url, language, content_hash, items[], custom_metadata).
- **POST view** at `apps/documents/views.py` — class-based `APIView` for `POST /v1/tenants/<tenant_id>/bots/<bot_id>/documents`. Validates URL slug params via Phase 2's `slug_validator`. Calls the pipeline. Maps pipeline errors to HTTP status codes.
- **URL routing** at `apps/documents/urls.py` + `config/urls.py` (modify) — wires the route under `/v1/`.
- **Typed exceptions** at `apps/documents/exceptions.py` — `UploadError` hierarchy.
- **Advisory lock context manager** at `apps/ingestion/locks.py` — Postgres `pg_advisory_lock`; basic acquire+release. NO timeout in 5a (5b adds it).
- **Pipeline orchestrator** at `apps/ingestion/pipeline.py` — basic flow: validate → acquire lock → get-or-create tenant/bot/collection → if doc_id exists, delete its chunks → chunk → embed → build payloads → upsert → save Document row → release lock → return.
- **Tests** + canned fixtures.

After Phase 5a: `POST /v1/tenants/test_tenant/bots/test_bot/documents` with a valid JSON body returns 201, the chunks land in Qdrant with the locked schema, the Document row is in Postgres, and re-uploading replaces the chunks. **No content_hash short-circuit yet (Phase 5b). No advisory lock timeout. No per-doc chunk cap.**

---

## Read first

- `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract; the public API of `embedder` / `chunker` / `payload` is what Phase 5a consumes.
- `build_prompts/phase_4_embedding_chunking/implementation_report.md` — confirms `devices=[...]` API and other deviations.
- `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract; `get_or_create_collection`, `delete_by_doc_id`, `get_qdrant_client`.
- `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract; `Tenant` / `Bot` / `Document` (note: `doc.bot_ref` not `doc.bot`), `slug_validator`, `validate_slug`, `advisory_lock_key`.
- `build_prompts/phase_1_foundation/spec.md` — locked stack, settings, structlog.
- `README.md` — project charter.
- `rag_system_guide.md` (if present) — §3 "Part A — Ingestion Flow" walks the same pipeline.

---

## Hard constraints

1. **Phase 1/2/3/4 are locked.** ONLY `config/urls.py` is modified (to add the `/v1/` include). No other prior-phase file touched.

2. **No new dependencies.** All needed packages (DRF, qdrant-client, FlagEmbedding, langchain-text-splitters) are already in `pyproject.toml`.

3. **URL pattern (locked):** `POST /v1/tenants/<str:tenant_id>/bots/<str:bot_id>/documents`. Use `<str:>` not `<slug:>` (Django's slug converter accepts hyphens, our regex doesn't). Validate via Phase 2's `slug_validator` in the view.

4. **`tenant_id` and `bot_id` from URL ONLY.** Never accepted from request body. The serializer rejects bodies that include those keys with a 400.

5. **Auto-create on first upload.** `Tenant.objects.get_or_create(tenant_id=...)`, `Bot.objects.get_or_create(tenant=..., bot_id=...)`. No CRUD endpoints needed.

6. **Use `Document.bot_ref` (not `Document.bot`).** Phase 2's E006 rename.

7. **Pipeline order (CRITICAL — locked):**
   ```
   1. Validate URL slugs (slug_validator on tenant_id, bot_id)
   2. Validate body (DRF serializer)
   3. Acquire pg_advisory_lock(advisory_lock_key(tenant_id, bot_id, doc_id))
   4. Tenant.objects.get_or_create
   5. Bot.objects.get_or_create
   6. get_or_create_collection (Phase 3)
   7. Look up existing Document by doc_id
   8. If existing: delete_by_doc_id (Phase 3)            ← PHASE 5A SIMPLE REPLACE
   9. Chunk each item.content (Phase 4 chunk_item)
   10. Embed all chunks (Phase 4 embed_passages)
   11. Build payloads (Phase 4 build_payload)
   12. Upsert all chunks via QdrantClient.upsert
   13. Document.objects.update_or_create (status="active")
   14. Release advisory lock (in finally)
   15. Return 201 { doc_id, chunks_created, items_processed,
                    collection_name, status: "created"|"replaced" }
   ```

8. **Phase 5a's replace is naive** — `delete_by_doc_id` then `upsert`. There's a brief window where the doc is missing from Qdrant. Acceptable in 5a; Phase 5b's content_hash short-circuit cuts the common case; v2's atomic version swap closes the window entirely.

9. **All chunks ALWAYS written with `version=1`, `is_active=True`** (Phase 4's `build_payload` already does this).

10. **Every chunk is a Qdrant `PointStruct` with all three vector types** — `dense`, `bm25` (via `SparseVector`), `colbert` (via list-of-lists).

11. **Errors map to HTTP status codes** per the table in "API contract" below. Error response body is always JSON `{"error": {"code", "message", "details"}}`.

12. **Slug validation in URL params** uses `apps.tenants.validators.validate_slug`. On failure, return 400 with `{"error": {"code": "invalid_slug", ...}}` BEFORE parsing the body.

13. **Pipeline errors are typed exceptions.** `UploadError` is the base; subclasses are `InvalidPayloadError` (400), `NoEmbeddableContentError` (422), `QdrantError` (500), `EmbedderLoadError` (500). The view catches them and returns the matching status.

14. **Tests use `@pytest.mark.django_db` for DB writes.** Tests that hit Qdrant use real Qdrant (skip-not-fail if unavailable, same pattern as Phase 3).

15. **No code comments unless the spec or a non-obvious invariant justifies them.**

16. **No emoji in code or comments. No `*.md` files beyond `implementation_report.md`.**

---

## API contract (locked)

**URL:** `POST /v1/tenants/<tenant_id>/bots/<bot_id>/documents`
**Auth:** None (DRF `AllowAny`)

**Request body:**

```json
{
  "doc_id": "<uuid string>",          // optional
  "source_type": "pdf|docx|url|html|csv|faq|image",
  "source_filename": "<str>",         // optional
  "source_url": "<str>",              // optional
  "language": "<str>",                // optional
  "content_hash": "sha256:<hex>",     // optional
  "items": [
    {
      "item_index": 0,
      "item_type": "<str>",           // optional
      "title": "<str>",               // optional
      "section_path": ["<str>"],      // optional, default []
      "page_number": 1,               // optional
      "url": "<str>",                 // optional
      "language": "<str>",            // optional
      "content": "<str>"              // REQUIRED, non-empty after strip
    }
  ],
  "custom_metadata": {                // optional
    "category": "<str>",
    "tags": ["<str>"]
  }
}
```

**Response codes (Phase 5a):**

| Code | When | Body |
|------|------|------|
| 201 | Fresh upload OR re-upload (replaces) | `{ doc_id, chunks_created, items_processed, collection_name, status: "created" or "replaced" }` |
| 400 | Slug validation failure (URL params), DRF body validation failure, or `tenant_id`/`bot_id` in body | `{ error: { code, message, details } }` |
| 422 | `items[]` empty, OR all items have whitespace-only content, OR no chunks survive after chunker filters | `{ error: { code, message } }` |
| 500 | Qdrant unreachable after retries, embedder load failure, Postgres error | `{ error: { code, message } }` |

Phase 5a does NOT return 200/409; those are added in Phase 5b.

---

## Deliverables

```
qdrant_rag/
├── apps/documents/
│   ├── serializers.py            ← NEW
│   ├── views.py                  ← NEW
│   ├── urls.py                   ← NEW
│   ├── exceptions.py             ← NEW
│   └── (existing models.py + admin.py from Phase 2 unchanged)
├── apps/ingestion/
│   ├── locks.py                  ← NEW (basic acquire+release; 5b extends)
│   ├── pipeline.py               ← NEW (5b extends)
│   └── (existing files from Phase 4 unchanged)
├── config/urls.py                ← MODIFY (add include for v1/)
└── tests/
    ├── fixtures/
    │   ├── valid_pdf_doc.json    ← NEW
    │   ├── invalid_no_items.json ← NEW
    │   └── invalid_empty_content.json ← NEW
    └── test_upload.py            ← NEW
```

7 new + 1 modified = 8 changed files (plus 3 fixture JSON files).

---

## File-by-file specification

### `apps/documents/exceptions.py` (NEW)

```python
"""Typed exceptions for the upload pipeline.

The view catches `UploadError` subclasses and maps them to HTTP responses.
"""

from __future__ import annotations


class UploadError(Exception):
    """Base for all upload-pipeline errors. Never raised directly."""

    http_status = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidPayloadError(UploadError):
    http_status = 400
    code = "invalid_payload"


class NoEmbeddableContentError(UploadError):
    http_status = 422
    code = "no_embeddable_content"


class QdrantWriteError(UploadError):
    http_status = 500
    code = "qdrant_write_failed"


class EmbedderError(UploadError):
    http_status = 500
    code = "embedder_failed"
```

### `apps/documents/serializers.py` (NEW)

DRF serializers. Two: one for the body item, one for the body itself.

Key requirements:
- `doc_id` is a `UUIDField(required=False)`. If absent, view generates.
- `source_type` is a `ChoiceField` over `["pdf", "docx", "url", "html", "csv", "faq", "image"]`. Anything else → 400.
- `items` is a `ListField` of nested item-serializers. Min length 1 (empty → 422 in pipeline, but DRF can also enforce min_length=1 for early 400).
- `items[*].content` is `CharField(required=True, allow_blank=False)` — DRF rejects empty strings. Whitespace-only is allowed by DRF; pipeline rejects later.
- `items[*].section_path` is `ListField(child=CharField, required=False, default=list)`.
- `custom_metadata` is `DictField(required=False, default=dict)`. Inside it, `tags` (if present) is a list of strings.
- Reject `tenant_id` / `bot_id` in body via `validate(self, attrs)` method — raise `ValidationError` with code `"id_in_body"` if present.

Example structure:

```python
from rest_framework import serializers


class UploadItemSerializer(serializers.Serializer):
    item_index = serializers.IntegerField(min_value=0)
    item_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    title = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    section_path = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        default=list,
    )
    page_number = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    language = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    content = serializers.CharField(allow_blank=False)


class UploadBodySerializer(serializers.Serializer):
    SOURCE_TYPES = ["pdf", "docx", "url", "html", "csv", "faq", "image"]

    doc_id = serializers.UUIDField(required=False)
    source_type = serializers.ChoiceField(choices=SOURCE_TYPES)
    source_filename = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    source_url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    language = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    content_hash = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    items = UploadItemSerializer(many=True)
    custom_metadata = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        forbidden = {"tenant_id", "bot_id"} & set(self.initial_data.keys())
        if forbidden:
            raise serializers.ValidationError({
                "error_code": "id_in_body",
                "message": f"Body must not contain {sorted(forbidden)} — these come from the URL path.",
            })
        if not attrs.get("items"):
            raise serializers.ValidationError({
                "error_code": "empty_items",
                "message": "items[] must not be empty.",
            })
        return attrs
```

### `apps/documents/views.py` (NEW)

```python
"""POST /v1/tenants/<tenant_id>/bots/<bot_id>/documents"""

from __future__ import annotations

import logging
import uuid

from django.http import JsonResponse
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documents.exceptions import InvalidPayloadError, UploadError
from apps.documents.serializers import UploadBodySerializer
from apps.ingestion.pipeline import UploadPipeline, UploadResult
from apps.tenants.validators import InvalidIdentifierError, validate_slug

logger = logging.getLogger(__name__)


class UploadDocumentView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request: Request, tenant_id: str, bot_id: str) -> Response:
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

        # 2. Validate body
        serializer = UploadBodySerializer(data=request.data)
        if not serializer.is_valid():
            return _error_response(
                http_status=400,
                code="invalid_payload",
                message="Body validation failed.",
                details=serializer.errors,
            )
        body = serializer.validated_data

        # 3. Generate doc_id if absent
        doc_id = body.get("doc_id") or uuid.uuid4()

        # 4. Run the pipeline
        try:
            result: UploadResult = UploadPipeline.execute(
                tenant_id=tenant_id,
                bot_id=bot_id,
                doc_id=str(doc_id),
                body=body,
            )
        except UploadError as exc:
            logger.error(
                "upload_failed",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": str(doc_id),
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

        return Response(
            {
                "doc_id": result.doc_id,
                "chunks_created": result.chunks_created,
                "items_processed": result.items_processed,
                "collection_name": result.collection_name,
                "status": result.status,
            },
            status=201,
        )


def _error_response(*, http_status: int, code: str, message: str, details: dict | None = None) -> Response:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return Response(body, status=http_status)
```

### `apps/documents/urls.py` (NEW)

```python
from django.urls import path

from apps.documents.views import UploadDocumentView

urlpatterns = [
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/documents",
        UploadDocumentView.as_view(),
        name="upload-document",
    ),
]
```

### `config/urls.py` (MODIFY — add include for `/v1/`)

The Phase 1 file has a stub URL conf. Modify it to:

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),  # /healthz from Phase 1
    path("v1/", include("apps.documents.urls")),  # NEW: Phase 5a
]
```

The `include("apps.core.urls")` line stays unchanged (Phase 1's `/healthz`). The new `path("v1/", ...)` is the only addition.

### `apps/ingestion/locks.py` (NEW — basic, 5b extends)

```python
"""Postgres advisory lock context manager.

Phase 5a: blocking acquire + release. No timeout (Phase 5b adds it).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator

from django.db import connection

from apps.qdrant_core.naming import advisory_lock_key

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def upload_lock(tenant_id: str, bot_id: str, doc_id: str) -> Generator[None]:
    """Acquire pg_advisory_lock; release on exit."""
    key1, key2 = advisory_lock_key(tenant_id, bot_id, doc_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", [key1, key2])
        try:
            logger.debug(
                "advisory_lock_acquired",
                extra={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
            )
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key1, key2])
            logger.debug(
                "advisory_lock_released",
                extra={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
            )
```

### `apps/ingestion/pipeline.py` (NEW — basic, 5b extends)

```python
"""Upload pipeline orchestrator.

Phase 5a: validate → lock → get-or-create → [if exists, delete] →
chunk → embed → payload → upsert → save Document → release lock → return.
No content_hash short-circuit (Phase 5b).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.db import transaction
from qdrant_client.models import PointStruct, SparseVector

from apps.documents.exceptions import (
    EmbedderError,
    NoEmbeddableContentError,
    QdrantWriteError,
)
from apps.documents.models import Document
from apps.ingestion.chunker import chunk_item
from apps.ingestion.embedder import (
    colbert_to_qdrant,
    embed_passages,
    sparse_to_qdrant,
)
from apps.ingestion.locks import upload_lock
from apps.ingestion.payload import ScrapedItem, ScrapedSource, build_payload
from apps.qdrant_core.client import get_qdrant_client
from apps.qdrant_core.collection import (
    delete_by_doc_id,
    get_or_create_collection,
)
from apps.qdrant_core.exceptions import QdrantError
from apps.tenants.models import Bot, Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadResult:
    doc_id: str
    chunks_created: int
    items_processed: int
    collection_name: str
    status: str  # "created" or "replaced"


class UploadPipeline:
    @staticmethod
    def execute(
        *,
        tenant_id: str,
        bot_id: str,
        doc_id: str,
        body: dict,
    ) -> UploadResult:
        started = time.monotonic()
        with upload_lock(tenant_id, bot_id, doc_id):
            tenant, _ = Tenant.objects.get_or_create(
                tenant_id=tenant_id,
                defaults={"name": tenant_id},
            )
            bot, _ = Bot.objects.get_or_create(
                tenant=tenant,
                bot_id=bot_id,
                defaults={"name": bot_id},
            )
            try:
                collection_name = get_or_create_collection(tenant_id, bot_id)
            except QdrantError as exc:
                raise QdrantWriteError(
                    f"Collection get_or_create failed: {exc}",
                    details={"tenant_id": tenant_id, "bot_id": bot_id},
                ) from exc

            existing = Document.objects.filter(doc_id=doc_id).first()
            is_replace = existing is not None
            if is_replace:
                if existing.tenant_id != tenant_id or existing.bot_id != bot_id:
                    raise QdrantWriteError(
                        "doc_id collision across tenants/bots — refuse to overwrite.",
                        details={
                            "doc_id": doc_id,
                            "expected_tenant": tenant_id,
                            "found_tenant": existing.tenant_id,
                        },
                    )

            # Chunk every item.content; flatten to (item, chunk) tuples.
            items_data = body["items"]
            source_type = body["source_type"]
            flat: list[tuple[dict, "Chunk"]] = []
            for item_data in items_data:
                chunks = chunk_item(
                    item_data["content"],
                    source_type=source_type,
                    item_index=item_data["item_index"],
                )
                for c in chunks:
                    flat.append((item_data, c))

            if not flat:
                raise NoEmbeddableContentError(
                    "No chunks survived after chunking.",
                    details={"items_count": len(items_data)},
                )

            # Embed all chunks in one call (FlagEmbedding batches internally).
            try:
                texts = [c.text for _, c in flat]
                embeddings = embed_passages(texts)
            except Exception as exc:
                raise EmbedderError(
                    f"Embedder failed: {exc}",
                    details={"chunk_count": len(flat)},
                ) from exc

            # Build PointStructs.
            source = ScrapedSource(
                type=source_type,
                filename=body.get("source_filename"),
                url=body.get("source_url"),
                content_hash=body.get("content_hash") or "",
                language=body.get("language"),
            )
            custom_metadata = body.get("custom_metadata") or {}

            points: list[PointStruct] = []
            for i, (item_data, chunk) in enumerate(flat):
                item = ScrapedItem(
                    item_index=item_data["item_index"],
                    item_type=item_data.get("item_type"),
                    title=item_data.get("title"),
                    section_path=item_data.get("section_path") or [],
                    page_number=item_data.get("page_number"),
                    url=item_data.get("url"),
                    language=item_data.get("language"),
                )
                payload_dict = build_payload(
                    chunk,
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    doc_id=doc_id,
                    item=item,
                    source=source,
                    custom_metadata=custom_metadata,
                )
                sparse_qd = sparse_to_qdrant(embeddings["sparse"][i])
                points.append(
                    PointStruct(
                        id=payload_dict["chunk_id"],
                        vector={
                            "dense": embeddings["dense"][i].tolist()
                                if hasattr(embeddings["dense"][i], "tolist")
                                else list(embeddings["dense"][i]),
                            "bm25": SparseVector(
                                indices=sparse_qd["indices"],
                                values=sparse_qd["values"],
                            ),
                            "colbert": colbert_to_qdrant(embeddings["colbert"][i]),
                        },
                        payload=payload_dict,
                    )
                )

            # Replace path: delete old chunks first.
            if is_replace:
                try:
                    delete_by_doc_id(tenant_id, bot_id, doc_id)
                except QdrantError as exc:
                    raise QdrantWriteError(
                        f"delete_by_doc_id failed during replace: {exc}",
                        details={"doc_id": doc_id},
                    ) from exc

            # Upsert all points.
            try:
                client = get_qdrant_client()
                client.upsert(collection_name=collection_name, points=points)
            except Exception as exc:
                raise QdrantWriteError(
                    f"upsert failed: {exc}",
                    details={"doc_id": doc_id, "chunks": len(points)},
                ) from exc

            # Save Document row.
            with transaction.atomic():
                Document.objects.update_or_create(
                    doc_id=doc_id,
                    defaults={
                        "bot_ref": bot,
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "source_type": source_type,
                        "source_filename": body.get("source_filename"),
                        "source_url": body.get("source_url"),
                        "content_hash": body.get("content_hash") or "",
                        "chunk_count": len(points),
                        "item_count": len(items_data),
                        "status": Document.ACTIVE,
                        "error_message": None,
                    },
                )

            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "upload_succeeded",
                extra={
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "doc_id": doc_id,
                    "items_processed": len(items_data),
                    "chunks_created": len(points),
                    "is_replace": is_replace,
                    "elapsed_ms": elapsed_ms,
                },
            )

            return UploadResult(
                doc_id=doc_id,
                chunks_created=len(points),
                items_processed=len(items_data),
                collection_name=collection_name,
                status="replaced" if is_replace else "created",
            )
```

### `tests/fixtures/valid_pdf_doc.json`

A complete, realistic upload body for a 2-item PDF:

```json
{
  "source_type": "pdf",
  "source_filename": "refund_policy.pdf",
  "source_url": null,
  "language": "en",
  "content_hash": "sha256:a3f8c4b1e2",
  "items": [
    {
      "item_index": 0,
      "item_type": "page",
      "title": "Refund Policy — Cold Pizza",
      "section_path": ["Refund Policy", "Cold Pizza Returns"],
      "page_number": 1,
      "language": "en",
      "content": "We offer a full refund if your pizza arrives cold, provided you notify us within 20 minutes of delivery. Please reply with your order number and a photo of the pizza. Our customer support team will process your refund within 3-5 business days."
    },
    {
      "item_index": 1,
      "item_type": "page",
      "title": "Refund Policy — Wrong Order",
      "section_path": ["Refund Policy", "Wrong Order"],
      "page_number": 2,
      "language": "en",
      "content": "If you receive the wrong order, you can request a full refund or a replacement at no charge. Contact our support team within 30 minutes of delivery and provide your order number along with a photo of the incorrect items."
    }
  ],
  "custom_metadata": {
    "category": "policy",
    "tags": ["refunds", "delivery"]
  }
}
```

### `tests/fixtures/invalid_no_items.json`

```json
{
  "source_type": "pdf",
  "source_filename": "empty.pdf",
  "items": []
}
```

### `tests/fixtures/invalid_empty_content.json`

```json
{
  "source_type": "pdf",
  "source_filename": "blank.pdf",
  "items": [
    {
      "item_index": 0,
      "content": ""
    }
  ]
}
```

### `tests/test_upload.py` (NEW)

Integration tests using DRF's `APIClient`. Use a session-scoped fixture to skip the suite if Qdrant is unreachable.

Key tests (Phase 5a):

- `test_201_fresh_upload_with_server_generated_doc_id`
- `test_201_fresh_upload_with_client_supplied_doc_id`
- `test_201_replace_existing_doc_id`
- `test_chunks_in_qdrant_have_locked_payload_fields` — verify all 20 payload fields present after upsert
- `test_400_invalid_tenant_slug` (e.g., `Pizza-Palace` with hyphen + uppercase)
- `test_400_invalid_bot_slug`
- `test_400_missing_required_field` (e.g., source_type)
- `test_400_tenant_id_in_body`
- `test_400_empty_items`
- `test_422_all_items_empty_content` — using invalid_empty_content.json
- `test_auto_creates_tenant_and_bot_rows`
- `test_unsupported_source_type_returns_400`

Use unique `(tenant_id, bot_id)` per test to avoid cross-test pollution. Cleanup in teardown via Phase 3's `drop_collection()` and Django's transaction rollback for ORM.

```python
import json
import pathlib
import uuid

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

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


@pytest.mark.django_db
def test_201_fresh_upload(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    response = client.post(url, body, format="json")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == "created"
    assert data["chunks_created"] >= 1
    assert data["items_processed"] == 2
    assert "doc_id" in data
    assert data["collection_name"] == f"t_{tenant}__b_{bot}"


@pytest.mark.django_db
def test_201_replace_existing(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    r1 = client.post(url, body, format="json")
    assert r1.status_code == 201
    assert r1.json()["status"] == "created"
    r2 = client.post(url, body, format="json")
    assert r2.status_code == 201
    assert r2.json()["status"] == "replaced"


@pytest.mark.django_db
def test_400_invalid_tenant_slug(client):
    body = _load("valid_pdf_doc.json")
    response = client.post("/v1/tenants/Pizza-Palace/bots/sup/documents", body, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_slug"


@pytest.mark.django_db
def test_400_tenant_id_in_body(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["tenant_id"] = "evil_tenant"
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_400_empty_items(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("invalid_no_items.json")
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_422_all_items_empty_content(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("invalid_empty_content.json")
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    # DRF rejects allow_blank=False at the serializer layer → 400
    # If the serializer allows the field through (via whitespace-only content),
    # the pipeline would 422. Either is acceptable; test for "not 201".
    assert response.status_code in (400, 422)


@pytest.mark.django_db
def test_auto_creates_tenant_and_bot(client, fresh_bot):
    from apps.tenants.models import Bot, Tenant
    tenant, bot = fresh_bot
    assert not Tenant.objects.filter(tenant_id=tenant).exists()
    body = _load("valid_pdf_doc.json")
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 201
    assert Tenant.objects.filter(tenant_id=tenant).exists()
    assert Bot.objects.filter(tenant_id=tenant, bot_id=bot).exists()


@pytest.mark.django_db
def test_chunks_have_full_payload_in_qdrant(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    response = client.post(url, body, format="json")
    assert response.status_code == 201
    doc_id = response.json()["doc_id"]

    from apps.qdrant_core.client import get_qdrant_client
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    client_q = get_qdrant_client()
    name = f"t_{tenant}__b_{bot}"
    points, _ = client_q.scroll(
        collection_name=name,
        scroll_filter=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
        limit=10,
        with_payload=True,
    )
    assert len(points) >= 1
    p = points[0]
    payload = p.payload
    required = {
        "tenant_id", "bot_id", "doc_id", "chunk_id",
        "version", "is_active", "uploaded_at",
        "source_type", "source_filename", "source_url",
        "source_item_index", "source_content_hash",
        "section_title", "section_path", "page_number",
        "text", "char_count", "token_count",
        "category", "tags",
    }
    missing = required - set(payload.keys())
    assert not missing, f"Missing payload fields: {missing}"
    assert payload["tenant_id"] == tenant
    assert payload["bot_id"] == bot
    assert payload["is_active"] is True
    assert payload["version"] == 1
```

Mark integration tests with `@pytest.mark.integration` if you want a separate marker for slow tests. Phase 5a's tests load the embedder (real model) on first run — slow; subsequent tests in the same session are fast.

---

## Acceptance criteria

Phase 5a is complete when **all** of these pass:

1. `uv run ruff check .` — zero violations.
2. `uv run ruff format --check .` — zero changes needed.
3. `uv run python manage.py makemigrations --check --dry-run` — no pending migrations (Phase 5a adds no models).
4. `uv run python manage.py check` — exits 0.
5. Stack rebuild: `make down && make up && sleep 90 && make health` — green JSON.
6. From host shell with stack up:
   ```bash
   curl -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
        -H "Content-Type: application/json" \
        -d @tests/fixtures/valid_pdf_doc.json | python -m json.tool
   ```
   returns 201 with `chunks_created >= 2` and `status: "created"`.
7. Re-running the same curl returns 201 with `status: "replaced"`.
8. `curl -X POST http://localhost:8080/v1/tenants/Pizza-Palace/bots/sup/documents -H "Content-Type: application/json" -d @tests/fixtures/valid_pdf_doc.json -w "%{http_code}"` → 400.
9. `uv run pytest tests/test_upload.py -v` (with embedder available, e.g. inside container or after warmup) — green.
10. Phase 1/2/3/4 regression: `uv run pytest -v` (host) keeps prior tests green; `make health` still 200.

---

## Common pitfalls (Phase 5a)

1. **Forgetting `Document.bot_ref` (using `Document.bot` instead).** Phase 2's E006 rename is locked. Test imports will fail.

2. **`config/urls.py` `include("apps.documents.urls")` namespace collision.** Phase 1's `apps.core.urls` already serves `/healthz` at the root. Adding `apps.documents.urls` under `path("v1/", ...)` is the right pattern. Don't put it at root.

3. **DRF `ChoiceField` + `source_type` mismatch.** If `source_type=pdf2` arrives, DRF returns 400 "not a valid choice". Make sure SOURCE_TYPES list matches Phase 4's CHUNK_CONFIG keys.

4. **`UUIDField(required=False)` returns a `uuid.UUID` object, not a string.** When passing to the pipeline, convert via `str(doc_id)`.

5. **`Tenant.objects.get_or_create(tenant_id=...)` defaults dict.** Without `defaults={"name": ...}`, the new tenant has empty name (DRF / NOT NULL fail). Always set `defaults`.

6. **Bot.save() auto-populates collection_name.** Don't pass `collection_name=` to `get_or_create`. Phase 2's model handles it.

7. **`Tenant.objects.get_or_create` race condition.** Two parallel uploads both see tenant missing, both insert, second hits unique-key violation. Mitigation: catch `IntegrityError` and re-fetch. For Phase 5a v1, the advisory lock per-(tenant,bot,doc_id) doesn't prevent this race because two DIFFERENT doc_ids may target the same new tenant. Acceptable corner case; document it.

8. **Embedder loading on first request.** Cold workers take ~30s. The 60s gunicorn timeout should cover it. If you see 504 timeouts in tests, run `verify_setup.py --full` first to warm the workers.

9. **PointStruct `id` field requirement.** Qdrant requires the point ID to be a UUID OR an unsigned integer. The `chunk_id` format `{doc_id}__i{item_index}__c{chunk_index}` is a STRING, not a UUID. Qdrant's gRPC API may accept arbitrary strings or hash them — verify against installed qdrant-client. If it requires UUID-format strings, use `chunk_id` as a payload field and let Qdrant auto-assign a UUID `id`. The chunk_id stays in payload for filter queries.

10. **Test pollution.** Tests that share `(tenant_id, bot_id)` pairs collide. Use `f"test_t_{uuid.hex[:8]}"` per test. Drop the collection in fixture teardown.

---

## Out of scope for Phase 5a

These are explicitly Phase 5b's responsibility:

- content_hash short-circuit (200 no_change response)
- Advisory lock acquire timeout + 409 conflict response
- Per-doc chunk cap (5000 chunks max → 422)
- pipeline-level unit tests with mocked embedder (`tests/test_pipeline.py`)
- Lock concurrency tests (`tests/test_locks.py`)
- Comprehensive concurrent-upload integration tests

These are Phase 6's:

- DELETE endpoint

These are Phase 7's:

- gRPC search

Do not implement any of these in 5a.

---

## When you finish

1. Confirm all 10 acceptance criteria pass.
2. Commit:
   - `apps/documents/{serializers,views,urls,exceptions}.py`
   - `apps/ingestion/{locks,pipeline}.py`
   - `config/urls.py` (modified)
   - `tests/fixtures/{valid_pdf_doc,invalid_no_items,invalid_empty_content}.json`
   - `tests/test_upload.py`
   - `build_prompts/phase_5a_upload_core/implementation_report.md`
3. Verify NO Phase 1/2/3/4 source file modified except `config/urls.py`.
4. Output a short report.

That's Phase 5a. Phase 5b extends `pipeline.py` and `locks.py` with content_hash short-circuit, lock timeout, chunk cap, and the comprehensive test suite.
