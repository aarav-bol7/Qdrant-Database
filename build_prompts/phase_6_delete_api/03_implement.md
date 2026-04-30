# Phase 6 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **BUILD per the revised plan, VERIFY against the spec, REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_6_delete_api/spec.md` — re-read in full.
2. `build_prompts/phase_6_delete_api/plan.md` — revised plan from Step 2.
3. `build_prompts/phase_6_delete_api/plan_review.md` — critique. Don't re-litigate.
4. `build_prompts/phase_5b_upload_idempotency/spec.md` + implementation_report.md — Phase 5b's lock contract.
5. `build_prompts/phase_5a_upload_core/spec.md` + report — Phase 5a's view + URL patterns.
6. `build_prompts/phase_3_qdrant_layer/spec.md` + report — `delete_by_doc_id`.
7. `build_prompts/phase_2_domain_models/spec.md` + report — `Document.bot_ref`, `slug_validator`.
8. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.

If any required input is missing, abort.

---

## Hard rules

1. Follow the revised plan. Document deviations.
2. Build in plan order.
3. Run verification at every checkpoint.
4. Honor "Out of scope" — no gRPC search, no atomic version swap, no audit log, no bulk delete, no async.
5. Modify ONLY: `apps/documents/{urls,views,exceptions}.py`, `apps/ingestion/pipeline.py`. Add: `tests/test_delete.py`. NO modification to ANY other Phase 1-5 file.
6. No code comments unless spec/invariant justifies.
7. Never commit `.env`.
8. No emoji. No `*.md` beyond `implementation_report.md`.

---

## Implementation phases

### Phase A — Extend exceptions.py

Add `DocumentNotFoundError(UploadError)` per spec. Don't touch existing classes.

**Verify:**
```bash
uv run python -c "
from apps.documents.exceptions import (
    UploadError, InvalidPayloadError, NoEmbeddableContentError,
    QdrantWriteError, EmbedderError,
    ConcurrentUploadError, DocumentTooLargeError,
    DocumentNotFoundError,                  # NEW
)
e = DocumentNotFoundError('test')
assert e.http_status == 404 and e.code == 'document_not_found'
print('attrs ok')
"
```

### Phase B — Extend pipeline.py

Add `DeleteResult` dataclass and `DeletePipeline` class per spec. Reuse the existing imports; add `DocumentNotFoundError` to the imports.

**Verify:**
```bash
uv run python manage.py check                           # 0 issues
uv run python -c "
from apps.ingestion.pipeline import DeletePipeline, DeleteResult
print(DeletePipeline, DeleteResult)
"
```

### Phase C — Extend views.py

Add `DeleteDocumentView(APIView)` per spec. The class uses the existing `_error_response` helper from Phase 5a's views.py.

**Verify:**
```bash
uv run python manage.py check
uv run python -c "
from apps.documents.views import DeleteDocumentView, UploadDocumentView
print(DeleteDocumentView, UploadDocumentView)
"
```

### Phase D — Extend urls.py

Append the new `path("tenants/<str:tenant_id>/bots/<str:bot_id>/documents/<uuid:doc_id>", ...)` to the existing urlpatterns.

**Verify:**
```bash
uv run python manage.py check
uv run python manage.py shell -c "
from django.urls import reverse
print(reverse('upload-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))
print(reverse('delete-document', kwargs={
    'tenant_id': 'a1b', 'bot_id': 'c2d',
    'doc_id': '00000000-0000-0000-0000-000000000000',
}))
"
```
Expected:
- `/v1/tenants/a1b/bots/c2d/documents`
- `/v1/tenants/a1b/bots/c2d/documents/00000000-0000-0000-0000-000000000000`

### Phase E — Stack rebuild + manual smoke

```bash
make down && make up && sleep 90 && make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full   # warm embedder

# Full lifecycle smoke
DOC_ID=$(uuidgen)
sed "s/^{/{\"doc_id\":\"$DOC_ID\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json

# 201 — create
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\nHTTP %{http_code}\n"

# 204 — delete
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\nHTTP %{http_code}\n"

# 204 — idempotent re-delete
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\nHTTP %{http_code}\n"

# 404 — non-existent doc
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$(uuidgen) -w "\nHTTP %{http_code}\n"

# 400 — bad slug
curl -sS -X DELETE http://localhost:8080/v1/tenants/Bad-Slug/bots/sup/documents/$(uuidgen) -w "\nHTTP %{http_code}\n"

# 404 — malformed UUID (Django default 404 page, NOT our envelope)
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/not-a-uuid -w "\nHTTP %{http_code}\n"
```

Expected status codes: 201 → 204 → 204 → 404 → 400 → 404.

### Phase F — Tests

Create `tests/test_delete.py` per spec.

**Verify (inside container — embedder warm):**
```bash
docker compose -f docker-compose.yml exec web pytest tests/test_delete.py -v
```

All tests green.

**From host:**
```bash
uv run pytest tests/test_delete.py -v
```
May skip if embedder isn't on host. Acceptable (the `uploaded_doc` fixture loads BGE-M3).

### Phase G — Full suite + regression

```bash
docker compose -f docker-compose.yml exec web pytest -v        # all tests
uv run pytest -v                                                # host (skips embedder-loading tests if needed)
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run

# Phase 1-5 regression
make health                                                     # Phase 1
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v       # Phase 5a + 5b
docker compose -f docker-compose.yml exec web pytest tests/test_pipeline.py -v     # Phase 5b
docker compose -f docker-compose.yml exec web pytest tests/test_locks.py -v        # Phase 5b
uv run pytest tests/test_models.py tests/test_naming.py tests/test_chunker.py tests/test_payload.py -v   # Phase 2 + 4 (no embedder load)

git status --short                                              # ONLY Phase 6 files
```

Files in `git status --short` must be:
- `apps/documents/urls.py` (modified)
- `apps/documents/views.py` (modified)
- `apps/documents/exceptions.py` (extended)
- `apps/ingestion/pipeline.py` (extended)
- `tests/test_delete.py` (new)
- `build_prompts/phase_6_delete_api/implementation_report.md` (new)

Anything else is a deviation requiring justification.

---

## Self-review

After Phase G passes, run self-review against the **spec**.

For each acceptance criterion (10): pass/fail, command run, output, notes.
For each pitfall (10): avoided/hit/N/A, how confirmed.
For each "Out of scope" item: confirmed not implemented.

---

## Final report

Save to `build_prompts/phase_6_delete_api/implementation_report.md`. Standard structure (status, summary, criteria, pitfalls, regression check, deviations, files, verify-block, verdict).

---

## What "done" looks like

Output to chat:

1. Path to `implementation_report.md`.
2. Overall status: PASS / FAIL / PARTIAL.
3. Acceptance criteria score: X/10.
4. Phase 1+2+3+4+5a+5b regression: PASS / FAIL.
5. Recommended next step (Phase 7 unblocked? — gRPC search service).

Then **stop**.

---

## A note on honesty

If any test relied on a fragile assumption (e.g., DRF's 204 body shape), say so. If the cross-tenant collision test was hard to construct cleanly, document the workaround. The report is the contract.
