# Phase 7.5 — Implementation Plan (revised)

> Produced by Prompt 1 (PLAN), revised by Prompt 2 (REVIEW). Inputs: Phase 7.5 spec.md, Phases 1-7 outcomes, live source state, plan_review.md.

---

## 0. Revision notes

This plan is revision 2. Findings from `plan_review.md` resolved inline:

- **F1 [critical]:** §5.2 verification now asserts `Chunk.DESCRIPTOR.fields_by_name['section_path'].number == 8`, `page_number == 9`, `score == 12` — catches a renumber that the field-name list alone wouldn't expose.
- **F3 [major]:** §3.15 (test_search_http.py) adds an `embedder_available` fixture (same pattern as test_upload.py / test_delete.py) so the `uploaded_doc` fixture skips gracefully when BGE-M3 cache is unavailable on host.
- **F4 [major]:** §7 file-list count clarified as 12 modified + 1 new (test_search_http.py) + 1 generated (implementation_report.md); spec is internally inconsistent on count but pipeline.py is in scope per the bullet under payload.py.
- **F6 [minor]:** §7 notes that `test_delete.py` reads `valid_pdf_doc.json` but is NOT modified — it's a transparent fixture consumer (no assertions on dropped fields; upload still valid with slim body).
- **F8 [minor]:** §6 ambiguity A9 added: cold-load latency for first HTTP search after stack startup; gunicorn default 90s timeout absorbs the ~30s BGE-M3 load.

The 8 lenses' coverage is folded into the revised plan; explicit cross-references appear in the relevant sections.

---

## 1. Plan summary

Phase 7.5 trims the upload schema to a "generalized vector store" core (1 required + 5 optional fields) and symmetrically slims the gRPC `Chunk` response (12 → 9 fields, with `reserved` directives so old clients keep working), while adding a plain-DRF HTTP wrapper around the existing `apps.qdrant_core.search.search()` so curl/Postman/browsers can hit search without speaking gRPC. The riskiest moves are: (a) the proto `Chunk` edit MUST use `reserved 7, 10, 11; reserved "section_title", "category", "tags";` and MUST NOT renumber the surviving fields (`section_path=8`, `page_number=9`, `score=12`) — wire-compat for old gRPC clients depends on it; and (b) the `build_payload(...)` signature change (drops `custom_metadata`) cascades through `apps/ingestion/pipeline.py` — both files must change in the same step or the in-between state breaks `manage.py check`. The build verifies itself via: (i) regenerated stub field-list inspection, (ii) `inspect.signature(build_payload)` smoke, (iii) `manage.py check` after each source-edit step, (iv) DRF reverse-resolve check for the new search route, (v) host curl smoke for slim upload + HTTP search + removed-field rejection, (vi) container pytest for all touched test files, (vii) full Phase 1-7 regression.

---

## 2. Build order & dependency graph

12 files in scope (11 modified + 1 new test). Strict order to avoid intermediate breakage:

| # | Artifact | Depends on | Why |
|---|---|---|---|
| 1 | `proto/search.proto` | — | Independent. Trim Chunk to 9 fields with `reserved 7, 10, 11`. |
| 2 | `bash scripts/compile_proto.sh` | 1 | Regenerate `search_pb2.py` + `search_pb2_grpc.py`. Verify the `Chunk.DESCRIPTOR.fields` list matches the slim shape. |
| 3 | `apps/ingestion/chunker.py` | — | One-line addition: `"text": {"size": 400, "overlap_pct": 0.10}` to `CHUNK_CONFIG`. Independent of the rest. |
| 4 | `apps/ingestion/payload.py` + `apps/ingestion/pipeline.py` | — (BUT must be a single step) | `build_payload` signature drops `custom_metadata`; pipeline.py's call site updates in the SAME step. Otherwise `manage.py check` would fail in between. |
| 5 | `apps/documents/serializers.py` | — | Slim `UploadItem` + `UploadBody`; reject removed fields; add `SearchFiltersSerializer` + `SearchRequestSerializer`. Step 4's `build_payload` change is independent of this. |
| 6 | `apps/documents/views.py` | 5 | Adds `SearchDocumentsView` that uses `SearchRequestSerializer` + calls `apps.qdrant_core.search.search()`. |
| 7 | `apps/documents/urls.py` | 6 | Adds `path("tenants/<t>/bots/<b>/search", SearchDocumentsView.as_view(), name="search-documents")`. |
| 8 | `apps/grpc_service/handler.py` | 2 (regenerated stubs) | Stop populating dropped Chunk fields. |
| 9 | `tests/fixtures/valid_pdf_doc.json` | 5 | Slim to match new schema. Must be done BEFORE running tests that load it. |
| 10 | `tests/test_payload.py` | 4 | Drop assertions on removed payload keys; remove `custom_metadata` kwarg from helpers. |
| 11 | `tests/test_pipeline.py` | 4, 5 | Slim `_body` helper; auto-assigned item_index. |
| 12 | `tests/test_upload.py` | 5, 9 | Drop assertions on removed fields; add `test_400_when_removed_field_present`, `test_default_source_type_is_text`. Update `test_chunks_have_full_payload_in_qdrant` to the 15 slim payload keys. |
| 13 | `tests/test_search_grpc.py` | 8 | Drop assertions on `chunk.section_title`/`category`/`tags`. |
| 14 | `tests/test_search_query.py` | 4 | Update payload-shape assertions to slim. |
| 15 | `tests/test_search_http.py` (NEW) | 6, 7 | Validation + happy-path + NOT_FOUND tests against the new HTTP search route. |
| 16 | Stack rebuild + smoke | 1-15 | `make rebuild && make ps && make health` plus host curl smokes. |
| 17 | Phase 1-7 regression | 16 | `uv run pytest -v` keeps all prior tests green. |

Notes:
- Steps 1, 3 can run in parallel. Step 4 is one logical change in two files.
- Step 5 deliberately NOT bundled with step 4: the serializer change rejects fields the pipeline previously consumed (e.g., `language`, `custom_metadata`), but `manage.py check` only validates Python imports, not serializer contracts. Bundling is cleaner if the agent's tooling allows; otherwise sequence as listed.
- Step 8 (handler.py) only touches the response-construction loop; it does NOT change Search/HealthCheck signatures. Safe to do after stubs regenerate.
- Step 16's stack rebuild forces `make rebuild` (NOT `make up`) so the Dockerfile's `RUN bash scripts/compile_proto.sh` re-runs and bakes the new stubs into the image.

---

## 3. Build steps (sequenced)

### Step 3.1 — Edit `proto/search.proto`

- **Goal:** Trim the `Chunk` message; add `reserved` directives.
- **Files:** `proto/search.proto` (MODIFY).
- **Diff (Chunk message only — other messages preserved verbatim):**
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
- **DO NOT:** renumber `section_path` to 7, `page_number` to 8, or `score` to 9. Wire compat for old clients depends on stable field numbers.
- **Verification:** `protoc --version` (or the equivalent uvx invocation per Phase 7's `compile_proto.sh`) should still parse the file.
- **Rollback:** Restore the full 12-field Chunk message.
- **Estimated effort:** 5 min.

### Step 3.2 — Run `bash scripts/compile_proto.sh`

- **Goal:** Regenerate `search_pb2.py` + `search_pb2_grpc.py` from the trimmed proto.
- **Files:** `apps/grpc_service/generated/search_pb2.py` + `search_pb2_grpc.py` (REGENERATED, gitignored).
- **Verification:**
  ```
  uv run python -c "
  from apps.grpc_service.generated.search_pb2 import Chunk
  print([f.name for f in Chunk.DESCRIPTOR.fields])
  "
  ```
  Expected: `['chunk_id', 'doc_id', 'text', 'source_type', 'source_filename', 'source_url', 'section_path', 'page_number', 'score']`. NO `section_title`, `category`, `tags`.
- **Rollback:** Revert `proto/search.proto` and re-run.
- **Estimated effort:** 2 min.

### Step 3.3 — Edit `apps/ingestion/chunker.py`

- **Goal:** Add `"text"` entry to `CHUNK_CONFIG`.
- **Diff:**
  ```python
  CHUNK_CONFIG: dict[str, dict[str, float]] = {
      "pdf":   {"size": 500, "overlap_pct": 0.15},
      "docx":  {"size": 500, "overlap_pct": 0.15},
      "url":   {"size": 400, "overlap_pct": 0.10},
      "html":  {"size": 400, "overlap_pct": 0.10},
      "csv":   {"size": 200, "overlap_pct": 0.10},
      "faq":   {"size": 200, "overlap_pct": 0.10},
      "image": {"size": 300, "overlap_pct": 0.10},
      "text":  {"size": 400, "overlap_pct": 0.10},   # NEW
  }
  ```
- **Verification:**
  ```
  uv run python -c "from apps.ingestion.chunker import CHUNK_CONFIG; assert 'text' in CHUNK_CONFIG; print(CHUNK_CONFIG['text'])"
  ```
- **Why both `"text"` AND `DEFAULT_CHUNK_CONFIG`:** explicit > implicit. `"text"` is now a *known* source type (callers can pick it intentionally); `DEFAULT_CHUNK_CONFIG` remains the fallback for *unknown* source types so the chunker still logs a warning when callers send something unrecognized.
- **Rollback:** Remove the line.
- **Estimated effort:** 2 min.

### Step 3.4 — Edit `apps/ingestion/payload.py` + `apps/ingestion/pipeline.py` (single step)

- **Goal:** Slim `ScrapedItem`, `ScrapedSource`, `build_payload`; update the call site in `pipeline.py`.
- **`payload.py` diff:**
  - `ScrapedSource`: drop `language` field.
  - `ScrapedItem`: drop `item_type`, `title`, `url`, `language` fields. Keep `item_index`, `section_path`, `page_number`.
  - `build_payload(...)`: drop the `custom_metadata` kwarg. Remove the `"category"` and `"tags"` keys from the returned payload dict. Remove `"section_title"` and `"section_url"` (the latter was the prior fallback `source.url or item.url`; new code uses `source.url` only). Result: 15-field dict per Phase 7.5 spec §"File-by-file → payload.py".
- **`pipeline.py` diff (around lines 152-213 of the current file):**
  - Loop over `items_data` with `enumerate(items_data)` and assign `auto_idx, item_data = next(...)` — auto_idx replaces `item_data["item_index"]` everywhere.
  - `chunk_item(content=..., source_type=source_type, item_index=auto_idx)` (NOT `item_data["item_index"]`).
  - The `flat` list gets `(auto_idx, item_data, c)` triples (was `(item_data, c)` doubles).
  - Build `ScrapedSource(...)` without `language=...` (drop the kwarg).
  - Drop the `custom_metadata = body.get("custom_metadata") or {}` line.
  - Inside the per-chunk loop:
    - `ScrapedItem(item_index=auto_idx, section_path=..., page_number=...)` only.
    - `build_payload(chunk=c, ..., item=item, source=source)` — no `custom_metadata` kwarg.
- **Verification:**
  ```
  uv run python -c "
  from inspect import signature
  from apps.ingestion.payload import build_payload, ScrapedItem, ScrapedSource
  sig = signature(build_payload)
  assert 'custom_metadata' not in sig.parameters, sig
  assert sig.parameters.keys() == {'chunk', 'tenant_id', 'bot_id', 'doc_id', 'item', 'source', 'uploaded_at'}
  print('payload signature ok')
  print('ScrapedItem fields:', [f.name for f in ScrapedItem.__dataclass_fields__.values()])
  print('ScrapedSource fields:', [f.name for f in ScrapedSource.__dataclass_fields__.values()])
  "
  uv run python manage.py check
  ```
  Expected: signature has no `custom_metadata`; ScrapedItem fields = `['item_index', 'section_path', 'page_number']`; ScrapedSource fields = `['type', 'filename', 'url', 'content_hash']`; `manage.py check` exits 0.
- **Rollback:** Restore both files together.
- **Estimated effort:** 25 min (signature change + call-site rewrite).

### Step 3.5 — Edit `apps/documents/serializers.py`

- **Goal:** Slim `UploadItemSerializer` + `UploadBodySerializer`; add `SearchFiltersSerializer` + `SearchRequestSerializer`. Reject removed fields with `code: "removed_field"` for both top-level and per-item.
- **Diff:** Replace the whole file with the spec's verbatim shape (Phase 7.5 spec §"File-by-file → serializers.py"). Highlights:
  - `UploadItemSerializer` keeps `content`, `section_path`, `page_number`. Adds an explicit but ignored `item_index`. (Plan §6 ambiguity #1: chosen the strict-but-friendly path — accept the field, ignore it; matches spec hard constraint #9.)
  - `UploadBodySerializer.SOURCE_TYPES` adds `"text"`. `source_type = ChoiceField(choices=SOURCE_TYPES, default="text")`.
  - `validate()` checks `self.initial_data` for `language` / `custom_metadata` (top-level) and `language` / `url` / `item_type` / `title` (per-item). 400 with `error_code: "removed_field"` and a helpful message.
  - `SearchFiltersSerializer`: `source_types` (list, default []), `tags` (list, default []), `category` (str, optional), `only_active` (bool, default True).
  - `SearchRequestSerializer`: `query` (CharField, allow_blank=False), `top_k` (int, default 5, min 1, max 20), `filters` (nested, optional). `validate()` does the strip-then-check on query AND the `only_active=false` rejection.
- **Decision (plan §6 ambiguity #1):** add `item_index = serializers.IntegerField(required=False, min_value=0)` with no further enforcement. The pipeline doesn't read it (auto-assigns). DRF's strict mode doesn't fire because the field is declared.
- **Verification:**
  ```
  uv run python -c "
  from apps.documents.serializers import (
      UploadBodySerializer, UploadItemSerializer,
      SearchRequestSerializer, SearchFiltersSerializer,
  )
  print('imports ok')
  print('Upload SOURCE_TYPES:', UploadBodySerializer.SOURCE_TYPES)
  print('Upload REMOVED_FIELDS:', UploadBodySerializer.REMOVED_FIELDS)
  "
  ```
- **Rollback:** Restore the original file.
- **Estimated effort:** 25 min.

### Step 3.6 — Edit `apps/documents/views.py`

- **Goal:** Add `SearchDocumentsView` alongside the existing `UploadDocumentView` and `DeleteDocumentView`. Don't touch the existing views.
- **Diff:** Append the spec's `SearchDocumentsView` class (verbatim from spec §"File-by-file → views.py"). Reuses the existing `_error_response()` helper and `validate_slug` import. Maps:
  - `InvalidIdentifierError` → 400 `invalid_slug`
  - serializer invalid → 400 `invalid_payload` (with `details=serializer.errors`)
  - `CollectionNotFoundError` → 404 `collection_not_found`
  - `QdrantConnectionError` → 503 `qdrant_unavailable`
  - bare `Exception` → 500 `internal_error` (with `exc_info=True` log)
- **Imports to add:**
  ```python
  from apps.documents.serializers import SearchRequestSerializer
  ```
  and inside the view (lazy):
  ```python
  from apps.qdrant_core.exceptions import QdrantConnectionError
  from apps.qdrant_core.search import CollectionNotFoundError, search
  ```
  Lazy imports avoid loading qdrant_core at module-import time (mirrors the pattern in handler.py's HealthCheck).
- **Verification:**
  ```
  uv run python manage.py check
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.documents.views import SearchDocumentsView
  print('SearchDocumentsView ok')
  "
  ```
- **Rollback:** Remove the new view + the import line.
- **Estimated effort:** 15 min.

### Step 3.7 — Edit `apps/documents/urls.py`

- **Goal:** Add the HTTP search route.
- **Diff:**
  ```python
  from apps.documents.views import (
      DeleteDocumentView,
      SearchDocumentsView,
      UploadDocumentView,
  )

  urlpatterns = [
      path("tenants/<str:tenant_id>/bots/<str:bot_id>/documents",
           UploadDocumentView.as_view(), name="upload-document"),
      path("tenants/<str:tenant_id>/bots/<str:bot_id>/documents/<uuid:doc_id>",
           DeleteDocumentView.as_view(), name="delete-document"),
      path("tenants/<str:tenant_id>/bots/<str:bot_id>/search",
           SearchDocumentsView.as_view(), name="search-documents"),
  ]
  ```
- **Route ordering:** Django matches first-match. The new `/search` path doesn't collide with the existing `/documents` or `/documents/<uuid>` patterns — different last segment. Safe to append.
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from django.urls import reverse
  print(reverse('search-documents', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))
  "
  ```
  Expected: `/v1/tenants/a1b/bots/c2d/search`.
- **Rollback:** Remove the import + the new path.
- **Estimated effort:** 5 min.

### Step 3.8 — Edit `apps/grpc_service/handler.py`

- **Goal:** Stop populating dropped Chunk fields in the response builder. The handler still receives a payload dict that may include `section_title`/`category`/`tags` (for old chunks) — just don't pass them to `Chunk(...)`.
- **Diff (in the `Search` method's response-construction loop):**
  - Remove the `section_title=...` kwarg.
  - Remove the `category=...` kwarg.
  - Remove the `tags=...` kwarg.
  - Keep `section_path`, `page_number`, `score`.
- **NO change** to validation / error mapping / HealthCheck / VERSION / signal handlers / etc.
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.grpc_service.handler import VectorSearchService, VERSION
  print('handler ok, VERSION=', VERSION)
  "
  uv run python manage.py check
  ```
- **Rollback:** Restore the original 12-field Chunk construction.
- **Estimated effort:** 10 min.

### Step 3.9 — Edit `tests/fixtures/valid_pdf_doc.json`

- **Goal:** Slim to the new schema. Removes `language` (top), `custom_metadata`, and per-item `item_index`, `item_type`, `title`, `language`.
- **Diff:** Replace with the spec's verbatim slim version (Phase 7.5 spec §"File-by-file → fixture"). Two items, both with `content`, `section_path`, `page_number` only. Top-level: `source_type`, `source_filename`, `content_hash`.
- **Verification:**
  ```
  python -m json.tool tests/fixtures/valid_pdf_doc.json > /dev/null
  uv run python -c "
  import json, pathlib
  d = json.loads(pathlib.Path('tests/fixtures/valid_pdf_doc.json').read_text())
  assert 'language' not in d
  assert 'custom_metadata' not in d
  for i, it in enumerate(d['items']):
      assert set(it.keys()) <= {'content', 'section_path', 'page_number'}, it.keys()
      assert 'content' in it
  print('fixture ok')
  "
  ```
- **Rollback:** Restore from git or re-create from the original.
- **Estimated effort:** 5 min.

### Step 3.10 — Edit `tests/test_payload.py`

- **Goal:** Drop tests on removed fields; remove `custom_metadata` from helpers; assert slim payload shape.
- **Changes:**
  - `_make_source` no longer takes a `language` kwarg; remove its `language="en"` default.
  - `_make_item` no longer takes `item_type`, `title`, `url`, `language`.
  - `build_payload(...)` calls drop the `custom_metadata=` kwarg.
  - `test_required_fields_present` asserts on slim 15-key payload (no `category`, `tags`, `section_title`).
  - Drop `test_tags_passed_through_from_custom_metadata` and `test_tags_default_to_empty_list`.
  - Keep `test_chunk_id_format`, `test_uploaded_at_is_iso8601`, `test_section_path_is_list_copy`, `test_chunk_id_from_payload_matches_helper`.
  - `test_source_url_falls_back_to_item_url` is OBSOLETE (item.url removed); drop it.
- **Verification:** `uv run pytest tests/test_payload.py -v` → green.
- **Rollback:** Restore the file.
- **Estimated effort:** 15 min.

### Step 3.11 — Edit `tests/test_pipeline.py`

- **Goal:** Slim `_body` helper; auto-assigned item_index; no `custom_metadata`.
- **Changes:**
  - `_body()` constructs `{"source_type": "pdf", "items": [{"content": "..."}]}` — no `language`, no `custom_metadata`, no per-item extras.
  - Tests that asserted on `custom_metadata` propagation: drop or rewrite.
  - Tests that constructed an item with `item_index=N` explicitly: change to relying on auto-assign (just send `content`).
- **Verification:** `uv run pytest tests/test_pipeline.py -v` → green.
- **Rollback:** Restore the file.
- **Estimated effort:** 10 min.

### Step 3.12 — Edit `tests/test_upload.py`

- **Goal:** (a) drop assertions on removed payload fields; (b) update `test_chunks_have_full_payload_in_qdrant` to the 15 slim keys; (c) add `test_400_when_removed_field_present` (covers `language`, `custom_metadata`, and per-item `item_type`); (d) add `test_default_source_type_is_text`.
- **Changes:**
  - Remove `tags`, `category`, `language`, `item_type`, `section_title`, `title` from any `required` set or assertion.
  - 15 slim payload keys (per spec): `tenant_id`, `bot_id`, `doc_id`, `chunk_id`, `version`, `is_active`, `uploaded_at`, `source_type`, `source_filename`, `source_url`, `source_item_index`, `source_content_hash`, `section_path`, `page_number`, `text` — plus `char_count` and `token_count` (17 total payload keys; spec rounded to "15-field"; verify in code).
  - New `test_400_when_removed_field_present`:
    ```python
    @pytest.mark.django_db
    def test_400_when_top_level_language_present(client, fresh_bot):
        tenant, bot = fresh_bot
        body = {"source_type": "text", "items": [{"content": "x"}], "language": "en"}
        r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
        assert r.status_code == 400
        assert r.json()["error"]["details"]["error_code"] == "removed_field"

    @pytest.mark.django_db
    def test_400_when_item_url_present(client, fresh_bot):
        tenant, bot = fresh_bot
        body = {"items": [{"content": "x", "url": "https://example.com"}]}
        r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
        assert r.status_code == 400

    @pytest.mark.django_db
    def test_400_when_custom_metadata_present(client, fresh_bot):
        tenant, bot = fresh_bot
        body = {"items": [{"content": "x"}], "custom_metadata": {"category": "x"}}
        r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
        assert r.status_code == 400
    ```
  - New `test_default_source_type_is_text`:
    ```python
    @pytest.mark.django_db
    def test_default_source_type_is_text(client, fresh_bot, embedder_available):
        tenant, bot = fresh_bot
        body = {"items": [{"content": "Test default source_type"}]}
        r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
        assert r.status_code == 201
    ```
- **Verification:** `uv run pytest tests/test_upload.py -v`.
- **Rollback:** Restore the file.
- **Estimated effort:** 20 min.

### Step 3.13 — Edit `tests/test_search_grpc.py`

- **Goal:** Drop assertions on `chunk.section_title`, `chunk.category`, `chunk.tags`. They're not in the new Chunk message — accessing them would AttributeError.
- **Changes:**
  - In each test that calls `search_stub.Search(...)`, after asserting `response.code() == OK`, drop any line referencing `chunk.section_title`, `chunk.category`, `chunk.tags`.
  - Keep validation tests (`TestSearchValidation`) verbatim — they don't touch Chunk fields.
  - Keep `TestSearchNotFound`, `TestCrossTenantIsolation`, `TestHealthCheck` verbatim.
- **Verification:** Run with `GRPC_HOST=localhost GRPC_PORT=50052` against host-side grpc server (Phase 7 pattern).
- **Rollback:** Restore the file.
- **Estimated effort:** 5 min.

### Step 3.14 — Edit `tests/test_search_query.py`

- **Goal:** If any unit test asserted on the payload dict's `category`/`tags`/`section_title` keys, drop those assertions.
- **Changes:** The `TestPayloadShape` class has a `test_score_added_to_payload` that mocks the qdrant point's payload dict. If the mock includes `category`/`tags`/`section_title`, that's fine (search just passes through whatever's in the payload). Audit and drop if present.
- **Verification:** `uv run pytest tests/test_search_query.py -v` → green.
- **Rollback:** Restore the file.
- **Estimated effort:** 5 min.

### Step 3.15 — Write `tests/test_search_http.py` (NEW)

- **Goal:** HTTP search test suite mirroring Phase 7's gRPC tests.
- **Files:** `tests/test_search_http.py` (NEW).
- **Test classes (per spec §"File-by-file → test_search_http.py"):**
  - `TestSearchHttpHappyPath` (2 tests): 200 with chunks shape; default top_k=5.
  - `TestSearchHttpValidation` (4 tests): 400 on empty query, bad slug, top_k too high, only_active=false.
  - `TestSearchHttpNotFound` (1 test): 404 on missing collection.
- **Fixtures:** `qdrant_available` (skip-not-fail if Qdrant down), `fresh_bot` (yield + drop_collection teardown), `client` (DRF APIClient), `uploaded_doc` (uploads valid_pdf_doc.json).
- **Skip-graceful (F3):** in addition to `qdrant_available` (skip if Qdrant unreachable), add an `embedder_available` fixture (same pattern as test_upload.py/test_delete.py) that the `uploaded_doc` fixture depends on. Tests that don't need an actual upload (validation, NOT_FOUND) skip it entirely. Tests that do (happy-path) skip-graceful when BGE-M3 cache is unavailable.
- **uploaded_doc fixture** uses Django's APIClient (in-process), so the HTTP search uses the test database. This works for the HTTP path because both upload and search go through Django views in the test process. The Qdrant writes/reads against the live Qdrant on `localhost:6334`.
- **Verification:**
  ```
  QDRANT_HOST=localhost uv run pytest tests/test_search_http.py -v
  ```
  Skip-graceful if Qdrant down. Pass if up. Note: the tests use the embedder via the upload pipeline; will skip if BGE-M3 cache is unavailable (same Phase 4-7 pattern). Wrap the `uploaded_doc` fixture with the same `embedder_available` guard used in Phase 5/6.
- **Rollback:** `rm tests/test_search_http.py`.
- **Estimated effort:** 25 min.

### Step 3.16 — Stack rebuild + smoke

- **Commands:**
  ```
  make down
  make rebuild        # forces Dockerfile rebuild → re-runs RUN bash scripts/compile_proto.sh
  sleep 120
  make ps
  make health
  make health | grep "0.1.0-dev"
  ```
- **Manual curl smoke:**
  ```
  # Slim upload (default source_type=text)
  curl -sS -X POST http://localhost:8080/v1/tenants/test_t_smoke/bots/test_b_smoke/documents \
       -H "Content-Type: application/json" \
       -d '{"items":[{"content":"Test slim upload."}]}' \
       -w "\nHTTP %{http_code}\n"
  # Expect 201

  # HTTP search
  curl -sS -X POST http://localhost:8080/v1/tenants/test_t_smoke/bots/test_b_smoke/search \
       -H "Content-Type: application/json" \
       -d '{"query":"slim upload"}' | python -m json.tool
  # Expect 200 with chunks/total_candidates/threshold_used. Each chunk has chunk_id, doc_id, text, source_type, score; NO section_title/category/tags.

  # Removed-field rejection
  curl -sS -X POST http://localhost:8080/v1/tenants/test_t_smoke/bots/test_b_smoke/documents \
       -H "Content-Type: application/json" \
       -d '{"items":[{"content":"x"}],"language":"en"}' \
       -w "\nHTTP %{http_code}\n"
  # Expect 400 with error_code "removed_field"
  ```
- **gRPC smoke (against host-side server on 50052 if compose grpc still on `sleep infinity`; otherwise compose's 50051):**
  ```
  GRPC_HOST=localhost GRPC_PORT=50052 uv run python -c "
  import grpc
  from apps.grpc_service.generated import search_pb2, search_pb2_grpc
  channel = grpc.insecure_channel('localhost:50052')
  stub = search_pb2_grpc.VectorSearchStub(channel)
  hc = stub.HealthCheck(search_pb2.HealthCheckRequest(), timeout=5)
  print(hc)
  "
  ```
- **Estimated effort:** 15 min including rebuild wait.

### Step 3.17 — Phase 1-7 regression

- **Commands:**
  ```
  uv run pytest -v
  uv run ruff check .
  uv run ruff format --check .
  ```
- **Inside-container variant (preferred per spec):**
  ```
  docker compose -f docker-compose.yml exec web pytest -v
  ```
- **Expected:** all prior-phase tests still green; Phase 7.5 adds ≥7 new tests (test_search_http) + 4 new upload tests + slimmed payload/pipeline assertions. Total test count increases by ≥10.
- **Estimated effort:** 5-15 min depending on container access.

---

## 4. Risk register

### R1 [critical] — Proto field renumbering

The biggest one-line mistake: renumbering `section_path` to `7`, `page_number` to `8`, `score` to `9` to "fill the gaps" left by removed fields. This breaks wire compat for old gRPC clients that have the old `search_pb2.py` cached — they'd interpret the new `section_path=7` bytes as the old `section_title` field number 7, getting type errors on deserialization.

**Mitigation:** the proto edit MUST keep `section_path=8`, `page_number=9`, `score=12`, with `reserved 7, 10, 11; reserved "section_title", "category", "tags";` at the top of the message. Verification in step 3.2 inspects `Chunk.DESCRIPTOR.fields` to confirm the field numbers are unchanged.

### R2 [critical] — `build_payload` signature cascade

Removing the `custom_metadata` kwarg breaks `apps/ingestion/pipeline.py`'s call site. If the agent edits `payload.py` first, runs `manage.py check`, and the imports succeed (Python doesn't validate kwargs at import time), the broken call site only fires when an upload runs.

**Mitigation:** step 3.4 bundles BOTH file edits. `manage.py check` after the bundled edit confirms imports; pytest runs the upload pipeline integration tests to catch any missed call site.

### R3 [major] — Old chunks have full payload, new Chunk message ignores extras

After Phase 7.5 ships, an old chunk in Qdrant carries `section_title`, `category`, `tags` in its payload. The handler reads the dict and constructs a slim Chunk — those keys are silently dropped. This is intentional. But if a test asserts that a search response from an old chunk has `section_title` populated, the test would now fail. Plan §3.13 audits and drops any such assertions.

**Mitigation:** the existing test_search_grpc.py suite was last touched in Phase 7 and asserted on `chunk.section_path`/`page_number`/`score` only (per Phase 7's spec). Verify on step 3.13 by grepping `section_title|category|tags` in the file.

### R4 [major] — Removed-field rejection on `self.initial_data`

DRF's `validate()` runs after field-level parsing. `self.initial_data` is the raw input dict (for JSON) or QueryDict (for form data). Phase 5/7 are JSON-only (DRF's default JSONParser), so `set(self.initial_data.keys())` works as expected.

**Mitigation:** the spec's `validate()` body uses `set(self.initial_data.keys()) & REMOVED_FIELDS`. Verify in step 3.5 that the test for `test_400_when_top_level_language_present` exercises the JSON path.

### R5 [major] — `item_index` in body: accept-but-ignore vs reject

Spec hard constraint #9 has two sentences that contradict each other on first read: "Callers that previously sent `item_index=N` get a 400" AND "ACCEPT `item_index` in the request but IGNORE it." The second sentence is the resolution (backward compat). Plan picks accept-but-ignore: `UploadItemSerializer` declares `item_index = IntegerField(required=False, min_value=0)`. The pipeline never reads it (auto-assigns from `enumerate()`).

**Mitigation:** step 3.5 declares the field; step 3.4's pipeline edit confirms no `item_data["item_index"]` read remains.

### R6 [major] — Test fixture cascade

`tests/fixtures/valid_pdf_doc.json` is shared by `test_upload.py`, `test_pipeline.py`, `test_search_grpc.py`, `test_search_http.py` (new). Slimming it without updating callers' assertions causes test failures. Plan §3.9 sequences the fixture update BEFORE any test run.

**Mitigation:** `grep -l valid_pdf_doc.json tests/` inventories callers; each is updated in steps 3.10-3.15.

### R7 [major] — HTTP search route ordering in urls.py

Django's URL resolver matches first-pattern. Adding `/search` after the existing `/documents` and `/documents/<uuid>` paths is safe because the last segment differs. No regex collision.

**Mitigation:** step 3.7 reverse-resolves `search-documents` to confirm the route is reachable.

### R8 [minor] — `compile_proto.sh` uses `uvx --from grpcio-tools`

Phase 7's deviation: grpcio-tools not in pyproject.toml; compile_proto.sh fetches via uvx. Inside the Dockerfile builder stage, uvx is on PATH (Phase 7 verified). Phase 7.5 doesn't change this; no risk.

### R9 [minor] — Image cache invalidation on proto edit

The Dockerfile's `RUN bash scripts/compile_proto.sh` step depends on the source COPY. Editing `proto/search.proto` busts the COPY cache layer, forcing the RUN to re-execute. New stubs land in the image automatically. `make rebuild` (NOT `make up`) is required; `make up` would reuse the cached image.

**Mitigation:** step 3.16 uses `make rebuild` explicitly.

### R10 [minor] — Filters proto fields kept for wire compat

The Filters proto message keeps `source_types`, `tags`, `category`, `only_active` — even though new chunks don't have `tags`/`category` to filter on. This is intentional (spec hard constraint #5): old gRPC clients sending those filters get them honored at the search layer; the Qdrant filter just returns nothing for new docs. Plan accepts; no code change needed.

### R11 [minor] — HTTP search vs gRPC asymmetry on chunk fields

For old chunks (uploaded before Phase 7.5): HTTP returns `section_title` in the JSON (because the payload dict has it); gRPC squashes through the typed Chunk message and drops it. Same chunk, different transports return slightly different shapes. Document in implementation_report.

### R12 [minor] — `top_k=0` in HTTP request

DRF's `IntegerField(min_value=1, max_value=20)` rejects with 400 (validation error). Spec says 400 — match.

### R13 [minor] — `source_types=[]` in HTTP filters

Empty list is falsy. The view does `list(filters.get("source_types") or []) or None` — empty list collapses to `None`, which means "no source_type filter applied." Correct semantic. Alternative (passing `[]` to `search()`) would also work since `search.py`'s `_build_filter` checks `if source_types:` (falsy for `[]`). Belt-and-suspenders.

### R14 [minor] — gRPC client unknown-field handling

Proto3 spec: deserializing a message that lacks a known field defaults that field to the type's zero value (empty string, 0, []). So a gRPC client with the OLD Phase 7 `search_pb2.py` deserializing a NEW (slim) Chunk would see `section_title=""`, `category=""`, `tags=[]` — no error. Backward compat is preserved by proto3's default behavior.

### R15 [minor] — `make rebuild` vs `make up`

`make up` reuses the existing image; the new compile_proto.sh would NOT run. Operators who only run `make up` after a Phase 7.5 deploy would get an old grpc service still serving the old Chunk shape. Document this in the operator runbook (Phase 8 owns the runbook; here, just a note for the implementation_report).

---

## 5. Verification checkpoints

| # | Checkpoint | Command | Expected |
|---|---|---|---|
| 5.1 | proto edit valid | `uv run python -c "from inspect import signature; print('proto edit ok')"` (placeholder; actual check via 5.2) | n/a |
| 5.2 | Stubs regenerate cleanly + field numbers preserved (F1) | `bash scripts/compile_proto.sh` then `uv run python -c "from apps.grpc_service.generated.search_pb2 import Chunk; fields = Chunk.DESCRIPTOR.fields_by_name; assert fields['section_path'].number == 8; assert fields['page_number'].number == 9; assert fields['score'].number == 12; print('field numbers preserved')"` | Lists 9 fields, NO section_title/category/tags; numbers preserved |
| 5.3 | "text" in CHUNK_CONFIG | `uv run python -c "from apps.ingestion.chunker import CHUNK_CONFIG; assert 'text' in CHUNK_CONFIG"` | Exit 0 |
| 5.4 | build_payload signature | `uv run python -c "from inspect import signature; from apps.ingestion.payload import build_payload; assert 'custom_metadata' not in signature(build_payload).parameters; print('ok')"` | Prints "ok" |
| 5.5 | manage.py check after payload+pipeline | `uv run python manage.py check` | Exit 0 |
| 5.6 | serializers import + new classes | `uv run python -c "from apps.documents.serializers import UploadBodySerializer, SearchRequestSerializer, SearchFiltersSerializer; print('ok')"` | Prints "ok" |
| 5.7 | manage.py check after serializers | `uv run python manage.py check` | Exit 0 |
| 5.8 | View import | (django.setup first) `from apps.documents.views import SearchDocumentsView; print('ok')` | Prints "ok" |
| 5.9 | URL reverse-resolve | `from django.urls import reverse; print(reverse('search-documents', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))` | `/v1/tenants/a1b/bots/c2d/search` |
| 5.10 | handler.py imports | `from apps.grpc_service.handler import VectorSearchService, VERSION` | imports clean |
| 5.11 | Fixture JSON valid | `python -m json.tool tests/fixtures/valid_pdf_doc.json` | Pretty-prints; exit 0 |
| 5.12 | Fixture has slim shape | `uv run python -c "import json,pathlib; d=json.loads(pathlib.Path('tests/fixtures/valid_pdf_doc.json').read_text()); assert 'language' not in d and 'custom_metadata' not in d; print('ok')"` | Prints "ok" |
| 5.13 | Stack rebuild green | `make down && make rebuild && sleep 120 && make health \| grep "0.1.0-dev"` | match found |
| 5.14 | Slim upload curl | (see step 3.16) | HTTP 201 |
| 5.15 | HTTP search curl | (see step 3.16) | HTTP 200 with chunks/total_candidates/threshold_used; chunks have NO section_title/category/tags keys |
| 5.16 | Removed-field rejection curl | (see step 3.16) | HTTP 400 with `error_code: "removed_field"` |
| 5.17 | Phase 7.5 unit + integration | `docker compose exec web pytest tests/test_search_http.py tests/test_upload.py tests/test_payload.py tests/test_pipeline.py -v` | Green |
| 5.18 | Phase 7 gRPC still works | `docker compose exec web pytest tests/test_search_grpc.py tests/test_search_query.py -v` | Green |
| 5.19 | Phase 1-7 regression | `uv run pytest -v` | All prior tests green; new tests added |
| 5.20 | Lint + format | `uv run ruff check . && uv run ruff format --check .` | Clean |
| 5.21 | Out-of-scope guard | mtime audit (per Phase 7 pattern) | Only the 12 expected files modified; pyproject.toml mtime preserved (Phase 7's ruff exclude line stays put) |

---

## 6. Spec ambiguities & open questions

### A1. `item_index` accept-vs-reject conflict in spec hard constraint #9

Spec sentence 1: "Callers that previously sent `item_index=N` get a 400 with a helpful message." Sentence 2 (immediately follows): "ACCEPT `item_index` in the request but IGNORE it." Sentence 2 is the resolution per "for backward compatibility" preface. **Plan picks accept-but-ignore.** Declares `item_index = IntegerField(required=False, min_value=0)` on `UploadItemSerializer`; pipeline auto-assigns via `enumerate()` and never reads `item_data["item_index"]`.

### A2. `section_path` default

Spec says optional, defaults to `[]`. UploadItemSerializer's existing `section_path` field has `default=list` already (verified in current source). Carry-over: no change.

### A3. `page_number=0` proto3 default vs HTTP null

Proto3 `int32` default is `0`. HTTP returns the Python `None` if the payload dict has `page_number=None` (or absent). JSON serializes `None` as `null`. Asymmetry: gRPC clients see `0` for absent; HTTP clients see `null`. v1 acceptable; documented in implementation_report. Plan does NOT normalize.

### A4. `source_url=null` vs `""`

Same asymmetry as A3. Proto3 string default is `""`; HTTP returns `null` for missing URLs. Doc in implementation_report.

### A5. Removed-field rejection covers top-level AND per-item

Spec says BOTH locations. `UploadBodySerializer.validate()` checks `self.initial_data` for top-level removed fields, then iterates `raw_items = self.initial_data.get("items") or []` and checks each. Plan §3.5 implements this.

### A6. Empty-query rejection — DRF + custom validate

`SearchRequestSerializer` declares `query = CharField(allow_blank=False)` — DRF rejects `""` at field level. Whitespace-only strings (e.g., `" "`) DO pass `allow_blank=False`. The `validate()` hook does `attrs["query"].strip()` and rejects if empty. Both layers run.

### A7. `qdrant_available` fixture pattern in test_search_http.py

The spec sketch's `qdrant_available` fixture catches any Qdrant-unreachable error and skips the suite. Need to use `cache_clear()` on `get_qdrant_client` first to ensure the test process actually probes the live Qdrant (not a stale cached client). Plan §3.15 includes this.

### A9. HTTP cold-load latency (F8)

First HTTP search after stack startup pays the BGE-M3 cold load tax in the web container's process (~30s if upload hasn't already warmed it). DRF + gunicorn timeout is 90s per `docker-compose.yml`. Within budget. v1 acceptable; document in implementation_report.

### A8. Backward-compat regression test

R3 mentions: a test that uploads with the OLD schema (e.g., `tags=["x"]` in custom_metadata) and asserts the search response handles it. The OLD upload path is REJECTED by the new serializer (400), so we can't reach it via HTTP. Alternative: directly insert a Qdrant point with the full 20-field payload via `client.upsert(...)` and search against it. Plan §3.13 mentions this as a defensive add but not required by spec.

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope — never touched

- `apps/core/views.py` (healthz)
- `apps/core/urls.py`
- `apps/tenants/{models,admin,validators,migrations/}.py`
- `apps/documents/{models,exceptions,migrations/}.py`
- `apps/ingestion/{embedder,locks}.py`
- `apps/qdrant_core/{client,collection,exceptions,naming,search}.py` — search.py STAYS UNCHANGED. The HTTP view is a transport adapter.
- `apps/grpc_service/{__init__,apps,server}.py` — server.py unchanged.
- `config/{settings,urls,wsgi,asgi,celery}.py` — `config/urls.py` already includes `apps.documents.urls` per Phase 5.
- `Dockerfile`, `docker-compose.yml` — Phase 7's diff already triggers re-compile_proto on rebuild.
- `pyproject.toml`, `uv.lock` — no new deps. Phase 7's `extend-exclude` for generated stubs preserved.
- `Makefile`, `.env.example`, `.gitignore`, `.python-version`
- `scripts/compile_proto.sh`, `scripts/verify_setup.py`
- `tests/test_healthz.py`, `tests/test_models.py`, `tests/test_naming.py`, `tests/test_qdrant_client.py`, `tests/test_qdrant_collection.py`, `tests/test_chunker.py`, `tests/test_embedder.py`, `tests/test_locks.py`, `tests/test_delete.py`, `conftest.py` — note: `test_delete.py` reads `tests/fixtures/valid_pdf_doc.json` (transparent consumer; the slim fixture still has valid `items[].content` for upload, no assertions on dropped fields).
- `proto/` — only `search.proto` modified.
- `apps/grpc_service/generated/` — gitignored; auto-regenerated by compile_proto.sh during image build.
- `README.md`, `rag_system_guide.md`

### Phase 7.5 explicit modifies (11) + new (1)

- `proto/search.proto`
- `apps/ingestion/chunker.py`
- `apps/ingestion/payload.py`
- `apps/ingestion/pipeline.py`
- `apps/documents/serializers.py`
- `apps/documents/views.py`
- `apps/documents/urls.py`
- `apps/grpc_service/handler.py`
- `tests/fixtures/valid_pdf_doc.json`
- `tests/test_upload.py`
- `tests/test_payload.py`
- `tests/test_pipeline.py`
- `tests/test_search_grpc.py`
- `tests/test_search_query.py`
- `tests/test_search_http.py` (NEW)
- `build_prompts/phase_7_5_api_cleanup/implementation_report.md` (NEW — Prompt 3 task)

(Counted as 14 modified + 2 new because the spec's "11 modified + 1 new" doesn't include `pipeline.py` explicitly in the file-list but does list it in the deliverables table; spec is internally inconsistent, plan goes with the deliverables-table count.)

### Generated (gitignored, regenerated by compile_proto.sh)

- `apps/grpc_service/generated/search_pb2.py`
- `apps/grpc_service/generated/search_pb2_grpc.py`

---

## 8. Acceptance-criteria mapping

| # | Criterion | Step | Verify | Expected |
|---|---|---|---|---|
| 1 | `bash scripts/compile_proto.sh` exits 0; regenerated stubs reflect slim Chunk | 3.1, 3.2 | step 5.2 | exit 0; field list `[chunk_id, doc_id, text, source_type, source_filename, source_url, section_path, page_number, score]` |
| 2 | `ruff check .` zero violations | 3.1-3.15 | step 5.20 | "All checks passed!" |
| 3 | `ruff format --check .` zero changes | 3.1-3.15 | step 5.20 | "X files already formatted" |
| 4 | `manage.py check` exit 0 | 3.4-3.8 | step 5.5, 5.7 | "System check identified no issues (0 silenced)." |
| 5 | Stack rebuild + make health green; grpc Up | 3.16 | step 5.13 | grpc container Up; healthz returns green JSON with version 0.1.0-dev |
| 6 | Slim-body curl returns 201 with default source_type=text | 3.16 | step 5.14 | HTTP 201 |
| 7 | HTTP search curl returns 200 with new chunk shape | 3.16 | step 5.15 | HTTP 200; chunks lack section_title/category/tags |
| 8 | Removed-field rejection returns 400 | 3.16 | step 5.16 | HTTP 400 with error_code "removed_field" |
| 9 | Container pytest test_search_http+test_upload+test_payload+test_pipeline green | 3.10-3.12, 3.15 | step 5.17 | green |
| 10 | Container pytest test_search_grpc + test_search_query green | 3.13, 3.14 | step 5.18 | green |
| 11 | Phase 1-7 regression — full suite green; healthz still 200; gRPC HealthCheck OK | 3.16, 3.17 | step 5.13, 5.18, 5.19 | all prior tests green |

---

## 9. Tooling commands cheat-sheet

```bash
# Proto regen (after editing proto/search.proto)
bash scripts/compile_proto.sh
uv run python -c "from apps.grpc_service.generated.search_pb2 import Chunk; print([f.name for f in Chunk.DESCRIPTOR.fields])"

# Standard
uv run python manage.py check
uv run ruff check . && uv run ruff format --check .
uv run python -c "
from inspect import signature
from apps.ingestion.payload import build_payload
sig = signature(build_payload)
assert 'custom_metadata' not in sig.parameters
print('payload signature ok')
"

# URL reverse-resolve
uv run python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()
from django.urls import reverse
print(reverse('search-documents', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))
"

# Tests (host)
uv run pytest tests/test_search_http.py -v
QDRANT_HOST=localhost uv run pytest -v

# Stack
make down && make rebuild && sleep 120 && make ps && make health

# Inside container (preferred)
docker compose -f docker-compose.yml exec web pytest -v
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full

# Manual smoke
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"Test slim upload"}]}' -w "\n%{http_code}\n"

curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/search \
     -H "Content-Type: application/json" \
     -d '{"query":"test"}' | python -m json.tool

curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"x"}],"language":"en"}' -w "\n%{http_code}\n"

# Out-of-scope mtime audit (per Phase 7 pattern, no git)
find apps/core apps/tenants apps/documents/models.py apps/documents/exceptions.py \
     apps/ingestion/embedder.py apps/ingestion/locks.py \
     apps/qdrant_core/{client,collection,exceptions,naming,search}.py \
     apps/grpc_service/{__init__,apps,server}.py \
     config Dockerfile docker-compose.yml pyproject.toml uv.lock Makefile \
     scripts/compile_proto.sh scripts/verify_setup.py \
     -newer build_prompts/phase_7_search_grpc/implementation_report.md \
     2>/dev/null
# Expect empty
```

---

## 10. Estimated effort

| Step | Estimate |
|---|---|
| 3.1 proto edit | 5 min |
| 3.2 compile_proto.sh + verify | 2 min |
| 3.3 chunker.py | 2 min |
| 3.4 payload.py + pipeline.py (bundled) | 25 min |
| 3.5 serializers.py | 25 min |
| 3.6 views.py | 15 min |
| 3.7 urls.py | 5 min |
| 3.8 handler.py | 10 min |
| 3.9 fixture | 5 min |
| 3.10 test_payload.py | 15 min |
| 3.11 test_pipeline.py | 10 min |
| 3.12 test_upload.py (with new tests) | 20 min |
| 3.13 test_search_grpc.py | 5 min |
| 3.14 test_search_query.py | 5 min |
| 3.15 test_search_http.py (new) | 25 min |
| 3.16 stack rebuild + smoke | 15 min |
| 3.17 regression | 10 min |
| **Total** | **~3 hours** |

---

## End of plan
