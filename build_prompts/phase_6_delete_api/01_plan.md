# Phase 6 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not modify any file.**

---

## Required reading (in this order)

1. `README.md` — project charter.
2. `build_prompts/phase_6_delete_api/spec.md` — full Phase 6 spec. **Source of truth. Read twice.**
3. `build_prompts/phase_5b_upload_idempotency/spec.md` — `upload_lock(tenant_id, bot_id, doc_id, timeout_s=5.0)` is what Phase 6 reuses; `ConcurrentUploadError` for lock contention.
4. `build_prompts/phase_5b_upload_idempotency/implementation_report.md` — Phase 5b outcomes.
5. `build_prompts/phase_5a_upload_core/spec.md` — view + URL routing patterns Phase 6 mirrors.
6. `build_prompts/phase_5a_upload_core/implementation_report.md` — Phase 5a outcomes (especially the PointStruct id format choice; Phase 6 doesn't use PointStruct directly but verifies chunks via doc_id payload filter).
7. `build_prompts/phase_3_qdrant_layer/spec.md` — `delete_by_doc_id` is the Qdrant helper.
8. `build_prompts/phase_2_domain_models/spec.md` — `Document.status = "deleted"`, `slug_validator`.
9. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.

If `phase_6_delete_api/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_6_delete_api/plan.md
```

---

## What the plan must contain

### 1. Plan summary

3–5 sentences. What's being added? What's the riskiest part? How does the build verify itself?

### 2. Build order & dependency graph

Phase 6 modifies 4 files and adds 1 test file. Order:

- exceptions.py extension first (just one new class, no deps)
- pipeline.py extension second (depends on `DocumentNotFoundError` from exceptions, plus existing Phase 5b imports)
- views.py extension third (depends on `DeletePipeline` from pipeline + DRF + Phase 2's `validate_slug`)
- urls.py extension fourth (depends on `DeleteDocumentView`)
- test_delete.py last (depends on the working endpoint)

### 3. Build steps (sequenced)

8–10 numbered steps. Each: goal · files · verification · rollback.

Critical sequencing:
- Add `DocumentNotFoundError` BEFORE adding `DeletePipeline` (the pipeline imports it).
- Stack rebuild after pipeline.py + views.py + urls.py (URL routing must propagate).
- Manual curl smoke for 201 → 204 → 204 (idempotent) → 404 → 400 BEFORE running pytest.
- Phase 5 regression: existing `test_upload.py`, `test_pipeline.py`, `test_locks.py` must still pass.

### 4. Risk register

- **Cross-tenant doc_id collision returning 500 instead of 404.** If we naively call `Document.objects.get(doc_id=...)` instead of `.filter(...).first()` + tenant check, an existing-but-wrong-tenant doc returns the row but the view doesn't check. Spec mandates 404. Plan must include a tenant_id mismatch check.
- **`<uuid:doc_id>` URL converter.** Django's UUID converter accepts canonical UUID strings (with hyphens). It rejects non-UUID strings → default 404 page. Test asserts 404 status only, NOT the response body shape.
- **Phase 5's `upload_lock` raises `ConcurrentUploadError` on timeout.** The DELETE view must catch that exception type even though the operation is delete (not upload). Reusing the existing exception is locked.
- **`Document.save(update_fields=[...])` and `auto_now`.** The `last_refreshed_at` field has `auto_now=True`. To trigger the auto-update, include `"last_refreshed_at"` in `update_fields`. Phase 5b verified this pattern; Phase 6 reuses it.
- **Idempotent re-delete on a soft-deleted Document.** `delete_by_doc_id` is no-op (chunks already gone); Postgres update sets the same fields again. Returns 204. Test verifies.
- **Deleting on a tenant/bot that auto-creates.** If a client DELETEs a doc_id on a tenant/bot that doesn't exist in Postgres, the Document lookup returns nothing → 404. We do NOT auto-create tenants on DELETE (different from POST's behavior). Plan must verify.
- **Pipeline transaction safety.** If `delete_by_doc_id` succeeds but `Document.save()` fails, Qdrant is empty but Postgres still says status="active". On retry, `delete_by_doc_id` is no-op, save() retries. Idempotent. Document this.
- **Test fixture pollution.** `uploaded_doc` fixture creates a doc; `fresh_bot` cleanup drops the collection. But the soft-deleted Document row stays in Postgres. Tests that share `(tenant_id, bot_id)` would collide. The fresh-uuid-per-test pattern from Phase 5 handles this. Verify.
- **Embedder cold-load on the `uploaded_doc` fixture.** First test in a session pays ~30s. Acceptable.
- **Phase 1-5 regression** from any accidental modification. Mtime audit + `git status --short` check.

### 5. Verification checkpoints

8–10 with exact commands and expected outcomes:

- After exceptions.py: import smoke shows new class.
- After pipeline.py: `manage.py check` passes.
- After views.py: `manage.py check`.
- After urls.py: `python manage.py shell -c "from django.urls import reverse; print(reverse('delete-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d', 'doc_id': '00000000-0000-0000-0000-000000000000'}))"` — should print the canonical path.
- After stack rebuild: `make health` green.
- Manual curl smoke (POST then DELETE) — see spec.md acceptance criterion 6.
- After test_delete.py: `pytest tests/test_delete.py -v` (in container, embedder warm) — green.
- Phase 5 regression: `pytest tests/test_upload.py tests/test_pipeline.py tests/test_locks.py -v` still green.
- Full host suite: `uv run pytest -v` — embedder-loading tests skip gracefully on host.
- Out-of-scope guard: `git status --short` shows ONLY 4 modified files + test_delete.py + implementation_report.md.

### 6. Spec ambiguities & open questions

5–8 entries. Things to scrutinize:

- **`Document.objects.filter(doc_id=doc_id).first()` vs `.get()`.** `get()` raises `DoesNotExist` which Phase 6's pipeline would catch — converting to `DocumentNotFoundError`. `.first()` returns None which we explicitly check. Both work; the spec uses `.first()` for explicit None-check.
- **`response.content == b""` assertion.** DRF's `Response(status=204)` returns empty content. Verify this in a real test run.
- **Order of operations on delete failure.** Spec says: Qdrant FIRST, Postgres SECOND. If Postgres update fails after Qdrant succeeds, the chunks are gone but Document still says "active". Acceptable on retry. Plan should note this.
- **`Retry-After` header value.** `ConcurrentUploadError`'s `retry_after` defaults to 5. DRF `Response()` accepts header dict via `headers={}` kwarg OR direct assignment `response[key] = value`. Phase 5b uses the latter. Stick with it.
- **`delete_by_doc_id` against a non-existent collection.** Phase 3's helper returns 0 if the collection doesn't exist. Phase 6 accepts this — Document soft-delete still proceeds. No special case needed.
- **Auto-create on DELETE.** Spec says DELETE does NOT auto-create tenant/bot rows. So a DELETE on `/v1/tenants/never_existed/bots/x/documents/<uuid>` returns 404 (Document not found) without creating any rows. Verify.
- **`<uuid:doc_id>` accepts uppercase UUIDs?** Django's UUID converter is case-insensitive by default. The view receives a `uuid.UUID` object. `str(doc_id)` produces a lowercase canonical representation. Database lookup is case-insensitive on UUID column. Should be fine.

### 7. Files deliberately NOT created / NOT modified

Echo spec.md's "Out of scope" + the don't-touch list (every file under apps/* except the 4 explicitly extended; all of config/, all tests except test_delete.py, all build_prompts/* except phase_6_*).

### 8. Acceptance-criteria mapping

For all 10 criteria: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Stack
make up && sleep 90 && make health

# From host
uv run pytest tests/test_delete.py -v
uv run pytest -v
uv run ruff check . && uv run ruff format --check .
uv run python manage.py check

# Inside container (embedder warm)
docker compose -f docker-compose.yml exec web pytest tests/test_delete.py -v

# Manual curl smoke (full lifecycle)
DOC_ID=$(uuidgen)
sed "s/^{/{\"doc_id\":\"$DOC_ID\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\n%{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\n%{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\n%{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$(uuidgen) -w "\n%{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/Bad-Slug/bots/sup/documents/$(uuidgen) -w "\n%{http_code}\n"
```

### 10. Estimated effort

Per-step wallclock. Phase 6 should be the SHORTEST phase since Phase 1 — most logic is reuse.

---

## Output format

Single markdown file at `build_prompts/phase_6_delete_api/plan.md`. 250–500 lines (smaller scope than 5a/5b).

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Line count.
3. 5-bullet summary.
4. Spec ambiguities (titles).

Then **stop**.
