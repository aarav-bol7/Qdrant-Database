# Phase 7.5 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **BUILD per the revised plan, VERIFY against the spec, REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_7_5_api_cleanup/spec.md` — re-read in full.
2. `build_prompts/phase_7_5_api_cleanup/plan.md` — revised plan.
3. `build_prompts/phase_7_5_api_cleanup/plan_review.md` — critique.
4. `build_prompts/phase_7_search_grpc/spec.md` + report — Phase 7 contract.
5. `build_prompts/phase_5a_upload_core/spec.md` + report — Phase 5a contract.
6. `build_prompts/phase_5b_upload_idempotency/spec.md` + report — Phase 5b contract.
7. `build_prompts/phase_4_embedding_chunking/spec.md` + report — Phase 4 contract.

If any required input is missing, abort.

---

## Hard rules

1. Follow the revised plan. Document deviations.
2. Build in plan order.
3. Run verification at every checkpoint.
4. Honor "Out of scope" — no migration script, no removing payload indexes, no auth, no rate limit.
5. Modify ONLY the 11 files listed in spec + add `tests/test_search_http.py`. NO modification to ANY other Phase 1-7 file.
6. No code comments unless spec/invariant justifies.
7. Never commit `.env` or generated stubs.
8. **Use `reserved` for removed proto field numbers.** NEVER renumber.
9. **`build_payload` signature change AND pipeline call-site update happen in the SAME commit** (or staged so each step compiles). Don't leave the tree in a broken state mid-phase.
10. No emoji. No `*.md` beyond `implementation_report.md`.

---

## Implementation phases

### Phase A — proto + stub regen

Edit `proto/search.proto` per spec — add `reserved 7, 10, 11; reserved "section_title", "category", "tags";` and remove the `section_title`/`category`/`tags` field lines. DO NOT renumber `section_path` (8), `page_number` (9), or `score` (12).

Regenerate stubs:
```bash
bash scripts/compile_proto.sh
```

**Verify:**
```bash
uv run python -c "
from apps.grpc_service.generated.search_pb2 import Chunk
fields = [f.name for f in Chunk.DESCRIPTOR.fields]
print('Chunk fields:', fields)
assert 'section_title' not in fields, 'section_title was not removed'
assert 'category' not in fields, 'category was not removed'
assert 'tags' not in fields, 'tags was not removed'
assert 'section_path' in fields
assert 'page_number' in fields
assert 'score' in fields
print('proto trim ok')
"
```

### Phase B — chunker.py

Add `"text"` to `CHUNK_CONFIG`. No other change.

**Verify:**
```bash
uv run python -c "
from apps.ingestion.chunker import CHUNK_CONFIG
assert 'text' in CHUNK_CONFIG
assert CHUNK_CONFIG['text']['size'] == 400
print('chunker ok')
"
```

### Phase C — payload.py + pipeline.py (atomic)

Edit BOTH files together. The `build_payload` signature change and the pipeline call-site update must land in the same commit so `manage.py check` doesn't fail mid-phase.

`payload.py`:
- Slim `ScrapedItem` (drop `item_type`, `title`, `url`, `language`)
- Slim `ScrapedSource` (drop `language`)
- Slim `build_payload` (drop `custom_metadata` kwarg; produce 15-field dict)

`pipeline.py`:
- In `UploadPipeline.execute`: replace `for item_data in items_data` loop with `for auto_idx, item_data in enumerate(items_data)`.
- Use `auto_idx` as `item_index` everywhere — both in the chunker call AND in the `ScrapedItem` constructor.
- Drop reads of `item_data["item_type"]`, `item_data["title"]`, `item_data["url"]`, `item_data["language"]`.
- Drop `custom_metadata = body.get("custom_metadata") or {}` and the kwarg in `build_payload`.

**Verify:**
```bash
uv run python manage.py check
uv run python -c "
from inspect import signature
from apps.ingestion.payload import build_payload, ScrapedItem, ScrapedSource
assert 'custom_metadata' not in signature(build_payload).parameters
assert {'item_type', 'title', 'url', 'language'}.isdisjoint(set(ScrapedItem.__dataclass_fields__))
assert 'language' not in ScrapedSource.__dataclass_fields__
print('payload + pipeline ok')
"
```

### Phase D — serializers.py

Slim UploadItemSerializer (drop everything but content + section_path + page_number; allow + ignore item_index).

Slim UploadBodySerializer (drop language, custom_metadata; default source_type='text'; reject removed fields with code 'removed_field').

Add SearchFiltersSerializer + SearchRequestSerializer.

**Verify:**
```bash
uv run python -c "
from apps.documents.serializers import (
    UploadBodySerializer, UploadItemSerializer,
    SearchRequestSerializer, SearchFiltersSerializer,
)
# Empty body should fail validation
s = UploadBodySerializer(data={})
print('empty body valid:', s.is_valid())
assert not s.is_valid()

# Body with removed field 'language' rejects
s = UploadBodySerializer(data={'items': [{'content': 'x'}], 'language': 'en'})
print('removed-field body valid:', s.is_valid())
assert not s.is_valid()
assert 'removed_field' in str(s.errors).lower() or 'removed' in str(s.errors).lower()

# Minimal body (just items+content) succeeds
s = UploadBodySerializer(data={'items': [{'content': 'hello world'}]})
print('minimal body valid:', s.is_valid())
assert s.is_valid(), s.errors
assert s.validated_data['source_type'] == 'text'
print('serializers ok')
"
```

### Phase E — views.py

Add `SearchDocumentsView` per spec. Don't touch the existing `UploadDocumentView` and `DeleteDocumentView`.

**Verify:**
```bash
uv run python manage.py check
uv run python -c "
from apps.documents.views import (
    UploadDocumentView, DeleteDocumentView, SearchDocumentsView,
)
print('views ok')
"
```

### Phase F — urls.py

Add the `path("tenants/<str:tenant_id>/bots/<str:bot_id>/search", ...)` to urlpatterns.

**Verify:**
```bash
uv run python manage.py check
uv run python manage.py shell -c "
from django.urls import reverse
print(reverse('upload-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))
print(reverse('delete-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d', 'doc_id': '00000000-0000-0000-0000-000000000000'}))
print(reverse('search-documents', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))
"
```

### Phase G — handler.py

Update the gRPC `Search` handler to stop populating `section_title`, `category`, `tags` in the response Chunk. Other fields stay. Read the spec's snippet.

**Verify:**
```bash
uv run python -c "
import inspect
from apps.grpc_service import handler
src = inspect.getsource(handler)
assert 'section_title' not in src or '# removed' in src.lower()
assert 'category=' not in src
assert 'tags=' not in src
print('handler trim ok')
"
```

### Phase H — Test fixture + tests

Update `tests/fixtures/valid_pdf_doc.json` per spec.

Update existing tests to match the new schema:
- `tests/test_upload.py` — adjust the `test_chunks_have_full_payload_in_qdrant` `required` set to the 15 slim fields. Drop assertions on dropped fields. Add `test_400_when_removed_field_present` and `test_default_source_type_is_text`.
- `tests/test_payload.py` — adjust helpers + tests (no `custom_metadata`, no dropped fields).
- `tests/test_pipeline.py` — adjust `_body` helper, drop `item_index` from inputs.
- `tests/test_search_grpc.py` — drop `chunk.section_title`/`category`/`tags` assertions; assert `chunk.section_path` and `chunk.page_number` if they're populated.
- `tests/test_search_query.py` — adjust if it asserted on payload structure (probably minor).

Create `tests/test_search_http.py` per spec.

**Verify:**
```bash
uv run pytest tests/test_payload.py -v        # fast unit, no Qdrant
uv run pytest tests/test_pipeline.py -v       # mocked embedder
```

### Phase I — Stack rebuild

```bash
make down
make up
sleep 90                           # cold-rebuild + image regen
make ps                            # all healthy
make health                        # 200
```

If grpc container exits, check logs:
```bash
docker compose -f docker-compose.yml logs grpc --tail 50
```
The most likely failure: stub regen didn't pick up the new proto. Re-run `bash scripts/compile_proto.sh` and `make rebuild` if needed.

### Phase J — Manual smoke

```bash
# Minimum-body upload (default source_type=text)
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"Test slim upload — refund policy text."}]}' \
     -w "\nHTTP %{http_code}\n"
# Expect 201 with status: created

# HTTP search
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/search \
     -H "Content-Type: application/json" \
     -d '{"query":"refund"}' | python -m json.tool
# Expect 200 with chunks/total_candidates/threshold_used

# Removed-field rejection
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"x"}],"language":"en"}' \
     -w "\nHTTP %{http_code}\n"
# Expect 400 with code: "removed_field"

# Removed-field rejection inside items
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"x","title":"Removed Field"}]}' \
     -w "\nHTTP %{http_code}\n"
# Expect 400 with code: "removed_field"

# gRPC search still works (regression)
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
```

### Phase K — Tests inside container

```bash
docker compose -f docker-compose.yml exec web pytest -v
```

All tests green.

### Phase L — Phase 1-7 regression + ruff + final check

```bash
uv run pytest -v                                     # host (skips embedder-loading tests)
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run

git status --short                                   # ONLY Phase 7.5 files
```

Files in `git status --short` must be:
- `proto/search.proto` (modified)
- `apps/documents/serializers.py` (modified)
- `apps/documents/views.py` (modified)
- `apps/documents/urls.py` (modified)
- `apps/ingestion/chunker.py` (modified)
- `apps/ingestion/payload.py` (modified)
- `apps/ingestion/pipeline.py` (modified)
- `apps/grpc_service/handler.py` (modified)
- `tests/fixtures/valid_pdf_doc.json` (modified)
- `tests/test_upload.py` (modified)
- `tests/test_payload.py` (modified)
- `tests/test_pipeline.py` (modified)
- `tests/test_search_grpc.py` (modified)
- `tests/test_search_query.py` (modified, if affected)
- `tests/test_search_http.py` (new)
- `build_prompts/phase_7_5_api_cleanup/implementation_report.md` (new)

NOT in git (gitignored):
- `apps/grpc_service/generated/search_pb2.py` (regenerated by Dockerfile RUN)
- `apps/grpc_service/generated/search_pb2_grpc.py`

Anything else in the diff is a deviation requiring justification.

---

## Self-review

After Phase L passes, run self-review against the **spec**.

For each acceptance criterion (11): pass/fail, command run, output, notes.
For each pitfall (10): avoided/hit/N/A, how confirmed.
For each "Out of scope" item: confirmed not implemented.

---

## Final report

Save to `build_prompts/phase_7_5_api_cleanup/implementation_report.md`. Standard structure plus:

- **Proto changes** — confirm `reserved 7, 10, 11` and field numbers preserved for `section_path` (8), `page_number` (9), `score` (12).
- **build_payload signature change** — confirm `custom_metadata` kwarg removed; pipeline updated.
- **Backward compatibility check** — if practical, upload an OLD-schema doc by directly inserting Qdrant points with `section_title`/`category`/`tags`, then HTTP search — confirm response is valid (extra fields ignored gracefully). If too complex, skip + document as a Phase 8 deferred check.
- **Phase 1-7 regression** — full suite + git diff name-only to prove no out-of-scope edits.

---

## What "done" looks like

Output to chat:

1. Path to `implementation_report.md`.
2. Overall status: PASS / FAIL / PARTIAL.
3. Acceptance criteria score: X/11.
4. Phase 1-7 regression: PASS / FAIL.
5. Recommended next step (Phase 8 unblocked? — final phase).

Then **stop**.

---

## A note on honesty

If any test relied on the OLD schema and was hard to update, document the workaround. If proto3 field-renumber was tempting and you almost did it, flag the close call. The report is the contract — write it to be true, not flattering.
