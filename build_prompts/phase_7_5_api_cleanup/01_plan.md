# Phase 7.5 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not modify any file.**

---

## Required reading (in this order)

1. `README.md` — project charter.
2. `build_prompts/phase_7_5_api_cleanup/spec.md` — full Phase 7.5 spec. **Source of truth. Read twice.**
3. `build_prompts/phase_7_search_grpc/spec.md` — Phase 7 gRPC contract; Phase 7.5 trims the Chunk message + adds an HTTP wrapper around the same `search()` function.
4. `build_prompts/phase_7_search_grpc/implementation_report.md` — Phase 7 outcomes (RRF emulation, generated stubs).
5. `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's upload contract; Phase 7.5 amends the body schema.
6. `build_prompts/phase_5b_upload_idempotency/spec.md` — content_hash + lock; unchanged.
7. `build_prompts/phase_4_embedding_chunking/spec.md` — payload + chunker; both touched.
8. `build_prompts/phase_2_domain_models/spec.md` — `slug_validator`, `Document.bot_ref`.

If `phase_7_5_api_cleanup/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_7_5_api_cleanup/plan.md
```

---

## What the plan must contain

### 1. Plan summary

3–5 sentences. What's being added/removed? What's the riskiest part? How does the build verify itself?

### 2. Build order & dependency graph

Phase 7.5 modifies 11 files and adds 1. Order:

- `proto/search.proto` first (independent of code; trigger stub regen).
- `bash scripts/compile_proto.sh` to verify the new Chunk message regenerates cleanly.
- `apps/ingestion/chunker.py` — add `"text"` to CHUNK_CONFIG (no other changes).
- `apps/ingestion/payload.py` — slim ScrapedItem, ScrapedSource, build_payload (signature change: drop `custom_metadata`).
- `apps/documents/serializers.py` — slim UploadItem + UploadBody; add SearchFilters + SearchRequest.
- `apps/ingestion/pipeline.py` — drop the dropped item-fields plumbing; auto-assign item_index via enumerate().
- `apps/documents/views.py` — add SearchDocumentsView.
- `apps/documents/urls.py` — add HTTP search route.
- `apps/grpc_service/handler.py` — stop populating dropped Chunk fields.
- Tests updated last (after the source changes settle).
- Stack rebuild + smoke after all source changes.

### 3. Build steps (sequenced)

10–14 numbered steps. Each: goal · files · verification · rollback.

Critical sequencing:
- Trim `proto/search.proto` BEFORE running `compile_proto.sh` — otherwise stubs are stale.
- `payload.py`'s `build_payload` signature change must precede `pipeline.py`'s call-site update (or do them in the same step to avoid intermediate broken state).
- `serializers.py`'s removed-field rejection must align with the pipeline expecting only the slim fields. Same step.
- Test fixture update goes BEFORE running the test files (otherwise tests load the old fixture and fail).
- Stack rebuild AFTER all source changes are in place.

### 4. Risk register

- **Renumbering proto fields.** The spec mandates `reserved` for removed field numbers. Plan must verify the agent uses `reserved 7, 10, 11` and does NOT renumber `section_path`/`page_number`/`score`.
- **`build_payload` signature change cascades.** Pipeline calls it; tests call it. Update all call sites in the same commit (or staged so each step compiles).
- **Old chunks in Qdrant have `section_title`/`category`/`tags`.** New code reads payloads dict-style and ignores extras — so old data flows through. Plan must include a test (or assertion) that searching against an old-payload chunk works.
- **`source_type=text` default.** New code path: caller omits `source_type` → DRF default `"text"` applies → chunker uses CHUNK_CONFIG["text"]. Plan must verify all three steps connect.
- **Removed-field rejection wording.** When a body has `language`, the serializer returns 400 with `code: "removed_field"`. Plan should verify the error envelope is consistent with Phase 5/6's pattern.
- **HTTP search shares serializer file with upload.** Don't accidentally break upload validation while editing serializers.py.
- **`item_index` strict-but-friendly handling.** Spec says: accept the field in the body, ignore its value, auto-assign from position. Plan must verify the pipeline auto-assigns and never reads the body's value.
- **Generated stubs are gitignored.** After editing `proto/search.proto`, the agent runs `compile_proto.sh` locally to verify the regen works. The Dockerfile RUN handles it during image build.
- **gRPC clients caching the proto.** External clients with the OLD search.proto would see `section_title`/`category`/`tags` as unknown fields when they query for the new Chunk shape — proto3 handles this gracefully (skips unknown). Plan should note this for v2-aware clients.
- **Test fixture cascade.** `valid_pdf_doc.json` is read by upload, payload, pipeline, search_grpc, search_http tests. Plan must `grep -l valid_pdf_doc.json` to inventory all callers and adjust.
- **Phase 1–7 regression.** Phase 5a's `test_upload.py` may have assertions on `category`/`tags`; update or drop them. Phase 6's test_delete.py uses `uploaded_doc` fixture; should still work.

### 5. Verification checkpoints

8–12 with exact commands and expected outcomes:

- After `proto/search.proto` edit: `bash scripts/compile_proto.sh` exits 0; `apps/grpc_service/generated/search_pb2.py` regenerated; `python -c "from apps.grpc_service.generated.search_pb2 import Chunk; print([f.name for f in Chunk.DESCRIPTOR.fields])"` shows the slim field list (no section_title/category/tags).
- After chunker.py edit: `python -c "from apps.ingestion.chunker import CHUNK_CONFIG; assert 'text' in CHUNK_CONFIG"`.
- After payload.py edit: `manage.py check` clean; import smoke confirms `build_payload` signature has no `custom_metadata` kwarg.
- After serializers.py edit: `python -c "from apps.documents.serializers import UploadBodySerializer, SearchRequestSerializer, SearchFiltersSerializer"`.
- After pipeline.py edit: `manage.py check` clean.
- After views.py edit: `manage.py check` clean.
- After urls.py edit: `python manage.py shell -c "from django.urls import reverse; print(reverse('search-documents', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))"` prints the new route.
- After handler.py edit: import smoke; `manage.py check`.
- After fixture update: `python -m json.tool tests/fixtures/valid_pdf_doc.json` validates the JSON.
- Stack rebuild: `make down && make up && sleep 90 && make health` green.
- Manual curl smoke: minimum-body upload returns 201; HTTP search returns 200; removed-field rejection returns 400; gRPC search still works.
- Tests in container: `docker compose exec web pytest -v` all green.
- Phase 1-7 regression: `uv run pytest -v` (host) keeps all prior tests green.

### 6. Spec ambiguities & open questions

5–8 entries:

- **`item_index` accepted but ignored.** The serializer doesn't have `item_index` as a field, so DRF's strict-mode would reject it. Spec says ACCEPT but ignore. Resolution: either add `item_index` as an optional ignored field on `UploadItemSerializer`, OR set DRF to non-strict. Plan should pick — strict-but-add-as-ignored is cleaner.
- **`section_path` default.** Spec says optional, defaults to `[]` per the existing serializer. Verify carry-over.
- **`page_number=0`.** Proto3 default. Spec says int32 with `0=absent` convention. HTTP returns the Python `None` if absent in payload, JSON serializes as `null`. The asymmetry between gRPC (`0`) and HTTP (`null`) may surprise clients. Plan should document or normalize.
- **`source_url=null` vs `""`.** Proto3 string default is `""`. HTTP returns `null` for missing URLs. Same asymmetry. v1: live with it; document.
- **Removed-field rejection on both top-level AND inside items.** Spec includes both. Verify the validate() method checks both.
- **HTTP search `query` validation.** `allow_blank=False` rejects `""` but not `"   "`. The validate() method does `attrs["query"].strip()` for the empty-after-strip check. Plan should confirm both layers run.
- **Backward-compat assertion in the test.** Suggest adding a test that uploads with the OLD schema (with `category`, `tags`) and confirms the 400 fires. Prevents regression where someone re-adds the old fields.

### 7. Files deliberately NOT created / NOT modified

Echo spec.md's "Out of scope" + the don't-touch list (everything not in the 12-file modification list).

### 8. Acceptance-criteria mapping

For all 11 criteria: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Proto stubs
bash scripts/compile_proto.sh
python -c "from apps.grpc_service.generated.search_pb2 import Chunk; print([f.name for f in Chunk.DESCRIPTOR.fields])"

# Standard
uv run python manage.py check
uv run ruff check . && uv run ruff format --check .
uv run pytest -v
uv run pytest tests/test_search_http.py -v

# Stack
make up && sleep 90 && make health
make rebuild      # if Dockerfile changes affect compile_proto.sh trigger

# Inside container
docker compose exec web pytest -v
docker compose exec web python scripts/verify_setup.py --full

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
```

### 10. Estimated effort

Per step. Phase 7.5 is moderate scope — touches a lot of files but each change is small.

---

## Output format

Single markdown file at `build_prompts/phase_7_5_api_cleanup/plan.md`. 350–600 lines.

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Total line count.
3. 5-bullet summary of key sequencing decisions (especially: how proto regen, payload signature change, and pipeline call-site update are sequenced).
4. Spec ambiguities flagged in section 6 (titles).

Then **stop**.
