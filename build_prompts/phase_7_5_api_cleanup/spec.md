# Phase 7.5 — API Cleanup

> **Audience:** A coding agent building on top of verified-green Phases 1–7 at `/home/bol7/Documents/BOL7/Qdrant`. Phase 7.5 is a focused API-surface refactor that sits between Phase 7 and Phase 8.

---

## Mission

Two related changes that touch overlapping files:

1. **Strip the upload schema** to match the user's "generalized vector store" goal. Remove fields that don't drive algorithm behavior or aren't being used as filters: `language` (top + item), `items[].language`, `items[].url`, `items[].item_type`, `items[].title`, `custom_metadata` (which contained `category` + `tags`). Auto-assign `items[].item_index` from array position. Default `source_type` to `"text"` so callers can omit it.

2. **Symmetrically trim the gRPC Chunk response.** Stored payload should match what the API returns. Drop `section_title`, `category`, `tags` from the `Chunk` proto message. Keep `section_path` and `page_number` as optional citation context.

3. **Add an HTTP search endpoint.** A plain DRF view that wraps `apps.qdrant_core.search.search()` — same algorithm Phase 7's gRPC handler uses. Lets clients (curl, Postman, browsers, anything that can't easily speak gRPC) hit search via HTTP. **No algorithm duplication; both transports call the same underlying function.**

After Phase 7.5: the upload body has 1 required field + 5 optional; the gRPC Chunk message has 7 fields instead of 12; and `POST /v1/tenants/<t>/bots/<b>/search` returns the same shape as gRPC's `Search()` but as JSON.

---

## Read first

- `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's upload contract; this phase amends the body schema.
- `build_prompts/phase_5a_upload_core/implementation_report.md` — Phase 5a outcomes.
- `build_prompts/phase_5b_upload_idempotency/spec.md` — `content_hash` short-circuit, `upload_lock`. None of this changes.
- `build_prompts/phase_4_embedding_chunking/spec.md` — `payload.build_payload` and chunker config. This phase trims `ScrapedItem`, `ScrapedSource`, and `build_payload`.
- `build_prompts/phase_7_search_grpc/spec.md` — Phase 7's gRPC contract; this phase trims the `Chunk` message.
- `build_prompts/phase_7_search_grpc/implementation_report.md` — Phase 7 outcomes (RRF emulation, etc.).
- `build_prompts/phase_2_domain_models/spec.md` — `slug_validator`, `Document.bot_ref`.
- `README.md` — context.

---

## Hard constraints

1. **Phases 1–7 source files are locked** EXCEPT these explicit modifications:
   - `apps/documents/serializers.py` — slim UploadBody + UploadItem; add SearchFilters + SearchRequest
   - `apps/documents/views.py` — add SearchDocumentsView
   - `apps/documents/urls.py` — add HTTP search route
   - `apps/ingestion/payload.py` — slim ScrapedItem, ScrapedSource, build_payload
   - `apps/ingestion/chunker.py` — add `"text"` key to CHUNK_CONFIG
   - `apps/grpc_service/handler.py` — stop populating dropped Chunk fields
   - `proto/search.proto` — slim Chunk message
   - `tests/fixtures/valid_pdf_doc.json` — match new schema
   - `tests/test_upload.py`, `test_payload.py`, `test_pipeline.py`, `test_search_grpc.py`, `test_search_query.py` — adjust assertions
   No other prior-phase file modified.

2. **No new dependencies.**

3. **Backward compatibility at the storage layer.** Old chunks (uploaded with Phase 5/6/7 schema) keep all their payload fields. Search returns them; the new Chunk message simply ignores the dropped fields. NO MIGRATION SCRIPT in v1. To get a fully-slim corpus, re-upload — the naive replace flow handles it.

4. **The `apps.qdrant_core.search.search()` function and the search algorithm are unchanged.** Phase 7's gRPC handler keeps working identically. The HTTP view is a transport adapter, not an algorithm reimplementation.

5. **The Filters proto message stays as-is** (with its now-mostly-unused fields). gRPC clients that send `source_types`/`tags`/`category` filters STILL get them honored at the search layer — but new chunks don't have `tags`/`category` to filter by, so those filters return nothing for new docs. Document this clearly. Don't remove the Filters proto fields — would break existing gRPC clients. Just stop populating the corresponding payload fields.

6. **HTTP search returns 200 with `{chunks, total_candidates, threshold_used}`** — same JSON shape as the gRPC SearchResponse, deserialized.

7. **HTTP search uses the SAME validation as gRPC**: bad slug → 400; empty query → 400; top_k OOR → 400; `only_active=false` → 400; collection missing → 404; Qdrant unavailable → 503; other errors → 500.

8. **`source_type` becomes optional with default `"text"`.** CHUNK_CONFIG gets a new entry: `"text": {"size": 400, "overlap_pct": 0.10}` (same as DEFAULT_CHUNK_CONFIG). The choice constraint in the serializer expands to include `"text"`.

9. **`items[].item_index` is auto-assigned from array position** in the serializer or pipeline. Callers that previously sent `item_index=N` get a 400 with a helpful message ("`item_index` is auto-assigned from array position; remove it from the request") — strict for v1; can soften later.

   Actually — for backward compatibility, ACCEPT `item_index` in the request but IGNORE it; auto-assign from position. Don't surprise callers with breakage. Document that the field is ignored.

10. **`custom_metadata` block is removed entirely** from the upload schema. If a request contains `custom_metadata`, the serializer rejects with 400 (`custom_metadata is no longer accepted`). Strict — clients should know they're using stale schema.

11. **No code comments unless spec or invariant justifies. No emoji. No `*.md` beyond `implementation_report.md`.**

---

## API contract changes

### Upload — POST (HTTP) — slim body

```json
{
  "doc_id": "optional uuid",
  "source_type": "optional, one of [pdf, docx, url, html, csv, faq, image, text]; default 'text'",
  "source_filename": "optional string",
  "source_url": "optional string",
  "content_hash": "optional sha256:...",
  "items": [
    {
      "content": "REQUIRED string",
      "section_path": ["optional", "hierarchy"],
      "page_number": 1
    }
  ]
}
```

**Required:** `items[]`, `items[].content`.
**Auto-assigned:** `items[].item_index` (server uses array position).
**Removed:** `language`, `items[].language`, `items[].item_type`, `items[].title`, `items[].url`, `custom_metadata`.

Sending `language`, `items[].language`, etc. → 400 with `code: "removed_field"` and `details: {removed: ["language", ...]}`. Strict for v1.

### Search — gRPC (port 50051) — slim Chunk

```protobuf
message Chunk {
  string chunk_id = 1;
  string doc_id = 2;
  string text = 3;
  string source_type = 4;
  string source_filename = 5;
  string source_url = 6;
  repeated string section_path = 7;  // renumbered from 8
  int32 page_number = 8;             // renumbered from 9
  float score = 9;                   // renumbered from 12
}
```

Removed (do NOT renumber the fields BACK to 7 etc — that breaks proto compatibility for existing clients):
- field 7 was `section_title` → REMOVED
- field 8 was `section_path` → renumbered to 7
- field 9 was `page_number` → renumbered to 8
- field 10 was `category` → REMOVED
- field 11 was `tags` → REMOVED
- field 12 was `score` → renumbered to 9

**Wait: proto3 best practice is NEVER reuse field numbers.** Use `reserved` instead:

```protobuf
message Chunk {
  reserved 7, 10, 11;                 // section_title, category, tags
  reserved "section_title", "category", "tags";

  string chunk_id = 1;
  string doc_id = 2;
  string text = 3;
  string source_type = 4;
  string source_filename = 5;
  string source_url = 6;
  // 7 reserved (was section_title)
  repeated string section_path = 8;
  int32 page_number = 9;
  // 10, 11 reserved (was category, tags)
  float score = 12;
}
```

This keeps wire-format-compatible with old clients (they ignore unknown fields gracefully). DO NOT renumber.

### Search — HTTP (POST, new)

```
POST /v1/tenants/<tenant_id>/bots/<bot_id>/search
```

Request body:

```json
{
  "query": "REQUIRED non-empty string",
  "top_k": 5,
  "filters": {
    "source_types": ["pdf"],
    "tags": ["refunds"],
    "category": "policy",
    "only_active": true
  }
}
```

`filters` is optional. If omitted, defaults to `{"only_active": true}`. (`only_active=false` → 400, same as gRPC.)

Response 200:

```json
{
  "chunks": [
    {
      "chunk_id": "...",
      "doc_id": "...",
      "text": "...",
      "source_type": "pdf",
      "source_filename": "refund_policy.pdf",
      "source_url": null,
      "section_path": ["Refund Policy", "Cold Pizza Returns"],
      "page_number": 1,
      "score": 0.87
    }
  ],
  "total_candidates": 5,
  "threshold_used": 0.65
}
```

Status codes:

| Code | When |
|---|---|
| 200 | OK; `chunks` may be `[]` |
| 400 | Bad slug · empty query · top_k OOR · `only_active=false` · malformed body |
| 404 | Collection doesn't exist |
| 503 | Qdrant unavailable (after retries) |
| 500 | Internal error |

---

## Deliverables

```
qdrant_rag/
├── apps/documents/
│   ├── serializers.py        ← MODIFY (slim upload + add search)
│   ├── views.py              ← MODIFY (add SearchDocumentsView)
│   └── urls.py               ← MODIFY (add HTTP search route)
├── apps/ingestion/
│   ├── chunker.py            ← MODIFY (add "text" to CHUNK_CONFIG)
│   └── payload.py            ← MODIFY (slim ScrapedItem, ScrapedSource, build_payload)
├── apps/grpc_service/
│   └── handler.py            ← MODIFY (stop populating dropped Chunk fields)
├── proto/search.proto        ← MODIFY (slim Chunk with reserved field nums)
├── tests/
│   ├── fixtures/
│   │   └── valid_pdf_doc.json  ← MODIFY (slim)
│   ├── test_upload.py        ← MODIFY (drop assertions on removed fields)
│   ├── test_payload.py       ← MODIFY (slim)
│   ├── test_pipeline.py      ← MODIFY (slim body construction)
│   ├── test_search_grpc.py   ← MODIFY (Chunk field assertions)
│   ├── test_search_query.py  ← MODIFY (if it asserts on payload structure)
│   └── test_search_http.py   ← NEW
```

11 files modified + 1 new test file. Plus rerun `compile_proto.sh` to regenerate the gRPC stubs from the trimmed `proto/search.proto`.

---

## File-by-file specification

### `apps/documents/serializers.py` (MODIFY)

Replace the existing UploadItem + UploadBody serializers with the slim versions. Add SearchFilters + SearchRequest.

```python
from rest_framework import serializers


class UploadItemSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=False)
    section_path = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        default=list,
    )
    page_number = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    # item_index is accepted but ignored — auto-assigned from position by the pipeline.
    # Don't enforce; just don't pass through.


class UploadBodySerializer(serializers.Serializer):
    SOURCE_TYPES = ["pdf", "docx", "url", "html", "csv", "faq", "image", "text"]
    REMOVED_FIELDS = {
        "language",
        "custom_metadata",
    }
    REMOVED_ITEM_FIELDS = {
        "language",
        "url",
        "item_type",
        "title",
    }

    doc_id = serializers.UUIDField(required=False)
    source_type = serializers.ChoiceField(choices=SOURCE_TYPES, default="text")
    source_filename = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    source_url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    content_hash = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    items = UploadItemSerializer(many=True)

    def validate(self, attrs):
        forbidden_top = self.REMOVED_FIELDS & set(self.initial_data.keys())
        if forbidden_top:
            raise serializers.ValidationError({
                "error_code": "removed_field",
                "message": (
                    f"Fields {sorted(forbidden_top)} were removed in Phase 7.5. "
                    "Drop them from the request body."
                ),
            })

        forbidden_id = {"tenant_id", "bot_id"} & set(self.initial_data.keys())
        if forbidden_id:
            raise serializers.ValidationError({
                "error_code": "id_in_body",
                "message": f"Body must not contain {sorted(forbidden_id)} — these come from the URL path.",
            })

        # Reject removed fields inside items as well.
        raw_items = self.initial_data.get("items") or []
        for i, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            forbidden_item = self.REMOVED_ITEM_FIELDS & set(raw.keys())
            if forbidden_item:
                raise serializers.ValidationError({
                    "error_code": "removed_field",
                    "message": (
                        f"items[{i}] contains removed fields {sorted(forbidden_item)}. "
                        "Drop them."
                    ),
                })

        if not attrs.get("items"):
            raise serializers.ValidationError({
                "error_code": "empty_items",
                "message": "items[] must not be empty.",
            })
        return attrs


# ===== HTTP search serializers (new) =====

class SearchFiltersSerializer(serializers.Serializer):
    source_types = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )
    tags = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )
    category = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    only_active = serializers.BooleanField(default=True)


class SearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(allow_blank=False)
    top_k = serializers.IntegerField(required=False, default=5, min_value=1, max_value=20)
    filters = SearchFiltersSerializer(required=False)

    def validate(self, attrs):
        filters = attrs.get("filters") or {}
        if filters and not filters.get("only_active", True):
            raise serializers.ValidationError({
                "error_code": "only_active_must_be_true",
                "message": "filters.only_active must be true in v1.",
            })
        if not attrs.get("query", "").strip():
            raise serializers.ValidationError({
                "error_code": "empty_query",
                "message": "query must be non-empty after stripping.",
            })
        return attrs
```

### `apps/documents/views.py` (MODIFY)

Add `SearchDocumentsView` alongside the existing UploadDocumentView and DeleteDocumentView. Keep all existing views untouched.

```python
class SearchDocumentsView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request: Request, tenant_id: str, bot_id: str) -> Response:
        try:
            validate_slug(tenant_id, field_name="tenant_id")
            validate_slug(bot_id, field_name="bot_id")
        except InvalidIdentifierError as exc:
            return _error_response(http_status=400, code="invalid_slug", message=str(exc))

        serializer = SearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error_response(
                http_status=400, code="invalid_payload",
                message="Body validation failed.", details=serializer.errors,
            )
        body = serializer.validated_data
        filters = body.get("filters") or {}

        from apps.qdrant_core.exceptions import QdrantConnectionError
        from apps.qdrant_core.search import CollectionNotFoundError, search

        try:
            result = search(
                tenant_id=tenant_id,
                bot_id=bot_id,
                query=body["query"].strip(),
                top_k=body.get("top_k", 5),
                source_types=list(filters.get("source_types") or []) or None,
                tags=list(filters.get("tags") or []) or None,
                category=(filters.get("category") or None),
            )
        except CollectionNotFoundError:
            return _error_response(
                http_status=404, code="collection_not_found",
                message="No collection for this bot. Upload a document first.",
            )
        except QdrantConnectionError as exc:
            return _error_response(
                http_status=503, code="qdrant_unavailable", message=str(exc),
            )
        except Exception as exc:
            logger.error("http_search_failed", exc_info=True)
            return _error_response(
                http_status=500, code="internal_error", message=str(exc),
            )

        return Response(result, status=200)
```

The `result` dict has shape `{chunks: [...], total_candidates: int, threshold_used: float}`. Each chunk is a payload dict with `score` added — exactly what the gRPC handler returns, just serialized as JSON.

**Important:** when the payload comes from `search()`, it has whatever fields are stored (slim for new chunks, full for old chunks). The HTTP view passes through the dict as-is. Old chunks return with their full payload; new chunks return with the slim payload. This is intentional backward compat.

### `apps/documents/urls.py` (MODIFY)

Add the HTTP search route:

```python
from apps.documents.views import (
    DeleteDocumentView,
    SearchDocumentsView,
    UploadDocumentView,
)

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
    path(
        "tenants/<str:tenant_id>/bots/<str:bot_id>/search",
        SearchDocumentsView.as_view(),
        name="search-documents",
    ),
]
```

### `apps/ingestion/chunker.py` (MODIFY)

Add `"text"` to `CHUNK_CONFIG`:

```python
CHUNK_CONFIG: dict[str, dict[str, float]] = {
    "pdf":   {"size": 500, "overlap_pct": 0.15},
    "docx":  {"size": 500, "overlap_pct": 0.15},
    "url":   {"size": 400, "overlap_pct": 0.10},
    "html":  {"size": 400, "overlap_pct": 0.10},
    "csv":   {"size": 200, "overlap_pct": 0.10},
    "faq":   {"size": 200, "overlap_pct": 0.10},
    "image": {"size": 300, "overlap_pct": 0.10},
    "text":  {"size": 400, "overlap_pct": 0.10},  # NEW
}
```

### `apps/ingestion/payload.py` (MODIFY)

Slim `ScrapedItem`, `ScrapedSource`, `build_payload`:

```python
@dataclass(frozen=True)
class ScrapedSource:
    type: str
    filename: str | None = None
    url: str | None = None
    content_hash: str = ""
    # REMOVED: language


@dataclass(frozen=True)
class ScrapedItem:
    item_index: int
    section_path: list[str] = field(default_factory=list)
    page_number: int | None = None
    # REMOVED: item_type, title, url, language


def build_payload(
    chunk: Chunk,
    *,
    tenant_id: str,
    bot_id: str,
    doc_id: str,
    item: ScrapedItem,
    source: ScrapedSource,
    uploaded_at: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Build the slim 15-field Qdrant payload for one chunk."""
    now = uploaded_at or datetime.datetime.now(datetime.UTC)
    return {
        # Identity
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "doc_id": doc_id,
        "chunk_id": build_chunk_id(doc_id, item.item_index, chunk.chunk_index),
        # Lifecycle
        "version": 1,
        "is_active": True,
        "uploaded_at": now.isoformat(),
        # Source provenance
        "source_type": source.type,
        "source_filename": source.filename,
        "source_url": source.url,
        "source_item_index": item.item_index,
        "source_content_hash": source.content_hash,
        "section_path": list(item.section_path),
        "page_number": item.page_number,
        # Content
        "text": chunk.text,
        "char_count": chunk.char_count,
        "token_count": chunk.token_count,
    }
```

15 fields instead of the previous 20. Removed: `section_title`, `category`, `tags`. Removed from prior version's signature: `custom_metadata` parameter.

`build_chunk_id` is unchanged.

**Pipeline-side note:** `apps/ingestion/pipeline.py`'s `UploadPipeline.execute()` builds the `ScrapedItem` per item. Phase 7.5 changes:
- Drop the `item_type`, `title`, `url`, `language` reads from `item_data`.
- Auto-assign `item_index` via `enumerate()` rather than reading from the body.
- Drop the `custom_metadata` plumbing.

The pipeline edit is small but must be in scope — explicitly modify `apps/ingestion/pipeline.py`. Add it to the file list.

### `apps/ingestion/pipeline.py` (MODIFY — explicit addition to the file list)

Per the bullet above. Update `UploadPipeline.execute()` so:

```python
# OLD:
for item_data in items_data:
    chunks = chunk_item(...)
    for c in chunks:
        flat.append((item_data, c))
# ...
item = ScrapedItem(
    item_index=item_data["item_index"],
    item_type=item_data.get("item_type"),
    title=item_data.get("title"),
    section_path=item_data.get("section_path") or [],
    page_number=item_data.get("page_number"),
    url=item_data.get("url"),
    language=item_data.get("language"),
)
custom_metadata = body.get("custom_metadata") or {}
payload_dict = build_payload(..., custom_metadata=custom_metadata)

# NEW:
for auto_idx, item_data in enumerate(items_data):
    chunks = chunk_item(item_data["content"], source_type=source_type, item_index=auto_idx)
    for c in chunks:
        flat.append((auto_idx, item_data, c))
# ...
item = ScrapedItem(
    item_index=auto_idx,
    section_path=item_data.get("section_path") or [],
    page_number=item_data.get("page_number"),
)
payload_dict = build_payload(  # no custom_metadata kwarg
    chunk=c,
    tenant_id=tenant_id, bot_id=bot_id, doc_id=doc_id,
    item=item, source=source,
)
```

The serializer accepts but ignores `item_index` from the body (per the strict-but-friendly behavior).

### `apps/grpc_service/handler.py` (MODIFY)

The handler currently builds the response Chunk message by reading payload dict keys. Update so it stops populating the dropped fields. This is purely additive — Phase 7's gRPC service keeps working, the response message just becomes smaller.

```python
# In Search handler, when building response.chunks from result["chunks"]:
for chunk_dict in result["chunks"]:
    chunk_msg = search_pb2.Chunk(
        chunk_id=chunk_dict.get("chunk_id", ""),
        doc_id=chunk_dict.get("doc_id", ""),
        text=chunk_dict.get("text", ""),
        source_type=chunk_dict.get("source_type", ""),
        source_filename=chunk_dict.get("source_filename") or "",
        source_url=chunk_dict.get("source_url") or "",
        section_path=list(chunk_dict.get("section_path") or []),
        page_number=chunk_dict.get("page_number") or 0,
        score=float(chunk_dict.get("score", 0.0)),
    )
    response.chunks.append(chunk_msg)
```

The dict still contains old chunks' `section_title`/`category`/`tags` if they were uploaded before Phase 7.5. The handler simply doesn't reference those keys — they're silently ignored. Old data flows through cleanly.

### `proto/search.proto` (MODIFY)

```protobuf
message Chunk {
  reserved 7, 10, 11;
  reserved "section_title", "category", "tags";

  string chunk_id = 1;
  string doc_id = 2;
  string text = 3;
  string source_type = 4;
  string source_filename = 5;
  string source_url = 6;
  // 7 reserved (was section_title)
  repeated string section_path = 8;
  int32 page_number = 9;
  // 10 reserved (was category)
  // 11 reserved (was tags)
  float score = 12;
}
```

DO NOT renumber `section_path`/`page_number`/`score` to fill the gaps. Wire compatibility means old gRPC clients still work; their unknown-field handling skips reserved tags.

After editing the .proto, regenerate stubs:

```bash
bash scripts/compile_proto.sh
```

The Dockerfile already runs this at build time, so a `make rebuild` produces the new stubs in the image automatically.

### `tests/fixtures/valid_pdf_doc.json` (MODIFY)

Slim to match the new schema:

```json
{
  "source_type": "pdf",
  "source_filename": "refund_policy.pdf",
  "content_hash": "sha256:a3f8c4b1e2",
  "items": [
    {
      "content": "We offer a full refund if your pizza arrives cold, provided you notify us within 20 minutes of delivery. Please reply with your order number and a photo of the pizza. Our customer support team will process your refund within 3-5 business days.",
      "section_path": ["Refund Policy", "Cold Pizza Returns"],
      "page_number": 1
    },
    {
      "content": "If you receive the wrong order, you can request a full refund or a replacement at no charge. Contact our support team within 30 minutes of delivery and provide your order number along with a photo of the incorrect items.",
      "section_path": ["Refund Policy", "Wrong Order"],
      "page_number": 2
    }
  ]
}
```

No `language`, no `custom_metadata`, no `item_index`, no `title`, no `url` per item, no `item_type`, no `language` per item.

### `tests/test_upload.py` (MODIFY)

- Drop assertions that read removed payload fields (`section_title`, `category`, `tags`).
- Update `test_chunks_have_full_payload_in_qdrant`: change `required` set to the 15 slim fields.
- Add a new test: `test_400_when_removed_field_present` — sending a body with `language` or `custom_metadata` returns 400 with `code: "removed_field"`.
- Add a new test: `test_default_source_type_is_text` — POSTing without `source_type` succeeds (uses default).

### `tests/test_payload.py` (MODIFY)

- Drop tests that asserted on `category`, `tags`, `section_title`, `custom_metadata`.
- The `_make_source` and `_make_item` helpers no longer take the dropped kwargs.
- `build_payload` signature change: no `custom_metadata` kwarg.

### `tests/test_pipeline.py` (MODIFY)

- The `_body` helper no longer constructs `custom_metadata` or `item_index`.
- Mock-based tests focus on the slim happy path.

### `tests/test_search_grpc.py` (MODIFY)

- Drop assertions on `chunk.section_title`, `chunk.category`, `chunk.tags` (they're not in the new Chunk message).
- The `test_search_returns_relevant_chunks` test checks `chunk.section_path`, `chunk.page_number`, `chunk.score`.

### `tests/test_search_query.py` (MODIFY)

- Verify that `apps.qdrant_core.search.search()` returns dicts with the slim payload fields. The function itself doesn't change, but if tests asserted on payload structure they need updating.

### `tests/test_search_http.py` (NEW)

```python
import json
import pathlib
import uuid

import pytest
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


@pytest.fixture
def uploaded_doc(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    r = client.post(
        f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json"
    )
    assert r.status_code == 201, r.json()
    return tenant, bot, body["doc_id"]


@pytest.mark.django_db
class TestSearchHttpHappyPath:
    def test_200_search_returns_chunks_shape(self, client, uploaded_doc):
        tenant, bot, _doc_id = uploaded_doc
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "cold pizza refund"}, format="json")
        assert r.status_code == 200
        data = r.json()
        assert "chunks" in data
        assert "total_candidates" in data
        assert "threshold_used" in data
        for chunk in data["chunks"]:
            assert "chunk_id" in chunk
            assert "text" in chunk
            assert "score" in chunk
            assert chunk["score"] >= 0.65

    def test_default_top_k_is_5(self, client, uploaded_doc):
        tenant, bot, _ = uploaded_doc
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "refund"}, format="json")
        assert r.status_code == 200
        assert len(r.json()["chunks"]) <= 5


@pytest.mark.django_db
class TestSearchHttpValidation:
    def test_400_empty_query(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": ""}, format="json")
        assert r.status_code == 400

    def test_400_bad_tenant_slug(self, client):
        r = client.post(
            "/v1/tenants/Bad-Tenant/bots/sup/search",
            {"query": "x"}, format="json",
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_slug"

    def test_400_top_k_too_high(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "x", "top_k": 99}, format="json")
        assert r.status_code == 400

    def test_400_only_active_false(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(
            url,
            {"query": "x", "filters": {"only_active": False}},
            format="json",
        )
        assert r.status_code == 400


@pytest.mark.django_db
class TestSearchHttpNotFound:
    def test_404_when_collection_missing(self, client):
        tenant = f"never_{uuid.uuid4().hex[:8]}"
        bot = f"never_{uuid.uuid4().hex[:8]}"
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "x"}, format="json")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "collection_not_found"
```

---

## Acceptance criteria

Phase 7.5 is complete when **all** of these pass:

1. `bash scripts/compile_proto.sh` exits 0; the regenerated `search_pb2.py` reflects the trimmed Chunk message (no `section_title`/`category`/`tags`).
2. `uv run ruff check .` — zero violations.
3. `uv run ruff format --check .` — zero changes.
4. `uv run python manage.py check` — exits 0.
5. Stack rebuild: `make down && make up && sleep 90 && make health` — green JSON; all containers healthy/running including grpc.
6. From host with stack up:
   ```bash
   # Upload with the slim schema
   curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
        -H "Content-Type: application/json" \
        -d '{"items":[{"content":"Test content for slim upload."}]}' \
        -w "\n%{http_code}\n"
   # Expect 201, with default source_type="text"
   ```
7. From host: HTTP search returns 200 with chunk shape:
   ```bash
   curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/search \
        -H "Content-Type: application/json" \
        -d '{"query":"test content"}' | python -m json.tool
   ```
   Response has `chunks`, `total_candidates`, `threshold_used`. Each chunk has `chunk_id`, `doc_id`, `text`, `source_type`, `score`. Does NOT have `section_title`, `category`, `tags`.
8. From host: removed-field rejection:
   ```bash
   curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
        -H "Content-Type: application/json" \
        -d '{"items":[{"content":"x"}],"language":"en"}' \
        -w "\n%{http_code}\n"
   ```
   Expect 400 with `code: "removed_field"`.
9. `docker compose -f docker-compose.yml exec web pytest tests/test_search_http.py tests/test_upload.py tests/test_payload.py tests/test_pipeline.py -v` — green.
10. `docker compose -f docker-compose.yml exec web pytest tests/test_search_grpc.py tests/test_search_query.py -v` — green (gRPC search still works with the trimmed Chunk).
11. Phase 1–7 regression: full host suite `uv run pytest -v` green; `make health` 200; gRPC HealthCheck returns OK.

---

## Common pitfalls

1. **Renumbering proto fields after removing some.** ALWAYS use `reserved` for removed field numbers. Renumbering breaks wire-compat with existing gRPC clients.

2. **Forgetting to regenerate the gRPC stubs after editing `proto/search.proto`.** The `compile_proto.sh` Dockerfile RUN handles it on `make rebuild`. For local dev, run `bash scripts/compile_proto.sh` manually.

3. **`build_payload` signature mismatch.** Removing the `custom_metadata` kwarg is a breaking change to any caller. Phase 7.5 callers: only the pipeline. Update both.

4. **Pipeline reads `item_data["item_index"]`.** After Phase 7.5, the serializer no longer rejects `item_index` in the body, but the pipeline auto-assigns from `enumerate()`. Don't fall back to the body's value — auto-assign always.

5. **Old chunks in Qdrant have `section_title`/`category`/`tags`.** Search returns them in the dict; the new Chunk message ignores them. Confirm via test that searching against an old-payload chunk still returns valid response (no AttributeError).

6. **HTTP search response includes payload fields the new Chunk message excludes.** Old chunks return `section_title` in the HTTP JSON; the new gRPC Chunk message doesn't. **This is OK** — HTTP returns the payload dict as-is; gRPC squashes through a typed message. Document this asymmetry.

7. **`source_type=text` test that POSTs without source_type.** Verify the default applies. Easy to forget.

8. **`tests/fixtures/valid_pdf_doc.json` is shared across tests.** Updating it for the slim schema breaks any test that read the old fields. Audit grep `valid_pdf_doc.json` to see all callers.

9. **`SearchRequestSerializer.validate` checks `attrs.get("query", "").strip()`.** DRF's `CharField(allow_blank=False)` rejects `""`, but whitespace-only strings DO pass `allow_blank=False`. The strip-then-check is defensive.

10. **HTTP search 404 vs gRPC NOT_FOUND.** Same semantic — collection doesn't exist. The view catches `CollectionNotFoundError` and returns 404 with `code: "collection_not_found"`. Same code string as the spec says; clients can rely on it.

---

## Out of scope

- Migration script for existing chunks — none in v1; old data flows through cleanly.
- Removing the `Filters` proto fields (source_types/tags/category) — kept for wire compat.
- Removing the `category`/`source_url`/etc. payload INDEXES from Qdrant collections — they stay; new chunks just don't populate them. Phase 8 cleanup if desired.
- Adding more transports (gRPC-Web, GraphQL, etc.).
- Authentication.
- Rate limiting.

---

## When you finish

1. Confirm all 11 acceptance criteria pass.
2. Commit:
   - `apps/documents/{serializers, views, urls}.py` (modified)
   - `apps/ingestion/{chunker, payload, pipeline}.py` (modified)
   - `apps/grpc_service/handler.py` (modified)
   - `proto/search.proto` (modified)
   - `tests/fixtures/valid_pdf_doc.json` (modified)
   - `tests/test_upload.py`, `test_payload.py`, `test_pipeline.py`, `test_search_grpc.py`, `test_search_query.py` (modified)
   - `tests/test_search_http.py` (new)
   - `build_prompts/phase_7_5_api_cleanup/implementation_report.md` (new)
3. Verify NO Phase 1-7 file modified outside the explicit list. The generated stubs (`apps/grpc_service/generated/search_pb2*.py`) are gitignored — they update automatically via Dockerfile RUN.
4. Output a short report.

That's Phase 7.5. Phase 8 (Hardening & Ship) is the final phase: Prometheus metrics, structlog enrichment, RRF score-distribution verification, gRPC reflection, snapshot/backup, runbook, CI green, load smoke.
