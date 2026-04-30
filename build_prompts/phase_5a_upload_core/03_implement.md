# Phase 5a — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job: BUILD per the revised plan, VERIFY against the spec, REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_5a_upload_core/spec.md` — re-read in full.
2. `build_prompts/phase_5a_upload_core/plan.md` — the revised plan from Step 2.
3. `build_prompts/phase_5a_upload_core/plan_review.md` — the critique. Don't re-litigate.
4. `build_prompts/phase_4_embedding_chunking/spec.md` + implementation_report.md — Phase 4 contract.
5. `build_prompts/phase_3_qdrant_layer/spec.md` + implementation_report.md — Phase 3 contract.
6. `build_prompts/phase_2_domain_models/spec.md` + implementation_report.md — Phase 2 contract; remember `Document.bot_ref`.
7. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.
8. `README.md` — context.

If any spec/plan/review for Phase 5a is missing, abort.

---

## Hard rules

1. Follow the revised plan. Deviations justified in the final report.
2. Build in plan order.
3. Run verification at every checkpoint.
4. Honor "Out of scope" — no content_hash short-circuit, no advisory lock timeout, no chunk cap, no DELETE endpoint, no search.
5. Modify ONLY `config/urls.py` from prior phases — every other Phase 1/2/3/4 file is locked.
6. No code comments unless spec or invariant justifies.
7. Never commit `.env`.
8. Verify the FlagEmbedding `devices=[...]` API still works (Phase 4's locked deviation).
9. Verify Qdrant's `PointStruct` accepts the `chunk_id` string format (or implement workaround).
10. No emoji. No `*.md` beyond `implementation_report.md`.

---

## Implementation phases

### Phase A — Verify Qdrant `PointStruct` id format

Before writing pipeline.py:

```bash
uv run python -c "
from qdrant_client.models import PointStruct
# Try a string id like our chunk_id format
try:
    p = PointStruct(id='doc-uuid__i0__c0', vector=[0.0]*1024, payload={})
    print('PointStruct accepts string id:', repr(p.id))
except Exception as e:
    print(f'PointStruct REJECTS string id: {type(e).__name__}: {e}')
    # Try UUID
    import uuid
    p = PointStruct(id=str(uuid.uuid4()), vector=[0.0]*1024, payload={})
    print('PointStruct accepts UUID string id:', repr(p.id))
"
```

Document the result in your implementation report. If non-UUID strings are rejected, you must convert chunk_id → UUID (e.g., `uuid.uuid5(uuid.NAMESPACE_OID, chunk_id)`) when constructing PointStruct, while keeping the original chunk_id in payload for filter queries.

### Phase B — Exceptions

Create `apps/documents/exceptions.py` per spec.

**Verify:** import smoke.

### Phase C — Locks

Create `apps/ingestion/locks.py` per spec.

**Verify (with stack up):**
```bash
make up
sleep 60
docker compose -f docker-compose.yml exec web python -c "
from apps.ingestion.locks import upload_lock
with upload_lock('test_t', 'test_b', 'doc-1'):
    print('inside lock')
print('lock released')
"
```

### Phase D — Pipeline

Create `apps/ingestion/pipeline.py` per spec, adapting for Phase A's PointStruct id finding.

**Verify:** `manage.py check` exits 0.

### Phase E — Serializer

Create `apps/documents/serializers.py`.

**Verify:**
```bash
uv run python -c "
from apps.documents.serializers import UploadBodySerializer
s = UploadBodySerializer(data={})
print('valid:', s.is_valid())
print('errors:', dict(s.errors))
"
```
Should print `valid: False` and at least `source_type` + `items` errors.

### Phase F — View + URLs

Create `apps/documents/views.py` and `apps/documents/urls.py`. Modify `config/urls.py` to add the `path("v1/", include("apps.documents.urls"))` line.

**Verify:**
```bash
uv run python manage.py check                # 0 issues
uv run python manage.py shell -c "
from django.urls import reverse
print(reverse('upload-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))
"
```
Should print `/v1/tenants/a1b/bots/c2d/documents`.

### Phase G — Fixtures

Create the three JSON fixtures per spec.

**Verify:**
```bash
for f in tests/fixtures/*.json; do
    echo "$f"
    python -m json.tool "$f" > /dev/null && echo "  OK" || echo "  INVALID"
done
```

### Phase H — Tests

Create `tests/test_upload.py` per spec.

**Verify:** structure parses (don't run yet).
```bash
uv run python -c "import tests.test_upload; print('imports ok')"
```

### Phase I — Stack-level smoke (manual curl)

```bash
make down
make up
sleep 90                                                  # cold start incl. Phase 4 deps
make health                                               # Phase 1 regression
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full   # warm up workers

# Fresh upload
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/valid_pdf_doc.json | python -m json.tool

# Re-upload (replace)
DOC_ID=$(uuidgen)
sed "s/^{/{\"doc_id\":\"$DOC_ID\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-doc-id.json
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @/tmp/with-doc-id.json | python -m json.tool
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @/tmp/with-doc-id.json | python -m json.tool

# Bad slug
curl -sS -X POST http://localhost:8080/v1/tenants/Pizza-Palace/bots/sup/documents \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/valid_pdf_doc.json -w "\n%{http_code}\n"
```

Expected:
1. Fresh: 201 with `status: "created"`, `chunks_created >= 2`.
2. First re-upload: 201 with `status: "created"` (different DOC_ID).
3. Second re-upload (same DOC_ID): 201 with `status: "replaced"`.
4. Bad slug: 400 with `code: "invalid_slug"`.

### Phase J — Run pytest

From inside the container (where embedder cache exists):
```bash
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v
```

All tests green. From host:
```bash
uv run pytest tests/test_upload.py -v
```
May skip if embedder isn't on host. Acceptable.

### Phase K — Full suite + regression

```bash
docker compose -f docker-compose.yml exec web pytest -v        # all tests
uv run pytest -v                                                # excludes embedder-loading tests on host
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
make health                                                     # Phase 1 regression

git status --short                                              # ONLY Phase 5a files + config/urls.py
```

Files in `git status --short` must be:
- `apps/documents/serializers.py` (new)
- `apps/documents/views.py` (new)
- `apps/documents/urls.py` (new)
- `apps/documents/exceptions.py` (new)
- `apps/ingestion/pipeline.py` (new)
- `apps/ingestion/locks.py` (new)
- `config/urls.py` (modified)
- `tests/fixtures/valid_pdf_doc.json` (new)
- `tests/fixtures/invalid_no_items.json` (new)
- `tests/fixtures/invalid_empty_content.json` (new)
- `tests/test_upload.py` (new)
- `build_prompts/phase_5a_upload_core/implementation_report.md` (new)

Anything else is a deviation requiring justification.

---

## Self-review

After Phase K passes, run self-review against the **spec**.

For each acceptance criterion (10): pass/fail, command run, output, notes.

For each pitfall (10): avoided/hit/N/A, how confirmed.

For each "Out of scope" item: confirmed not implemented.

---

## Final report

Save to `build_prompts/phase_5a_upload_core/implementation_report.md`. Structure:

```markdown
# Phase 5a — Implementation Report

## Status
**OVERALL:** PASS / FAIL / PARTIAL

## Summary
- Files created: <N>
- Files modified outside Phase 5a scope: <N> (must be 1: config/urls.py only)
- Tests added: <N>
- Tests passing in container: <N>/<N>
- Tests passing on host (excluding embedder-loading tests): <N>/<N>
- Acceptance criteria passing: <N>/10

## Qdrant PointStruct id format finding
[Result of Phase A inspection. If non-UUID strings rejected, document the workaround and where it lives in code.]

## Acceptance criteria
[for all 10: result, command, output, notes]

## Pitfall avoidance
[for all 10: avoided/hit/N/A, how confirmed]

## Out-of-scope confirmation
[brief list]

## Phase 1 + 2 + 3 + 4 regression check
- Phase 1: /healthz still green: <output>
- Phase 2: tests/test_models.py + tests/test_naming.py still green: <output>
- Phase 3: tests/test_qdrant_collection.py still green: <output>
- Phase 4: tests/test_chunker.py + test_payload.py + test_embedder.py still green: <output>
- No prior-phase file modified except config/urls.py: paste `git diff --name-only` output

## Deviations from plan
[for each: what · why · impact]

## Spec defects discovered
[anything in spec.md that turned out incorrect]

## Outstanding issues
[non-blocking]

## Files created or modified
[clean tree]

## Commands to verify the build (one block, copy-pasteable)

```bash
make down
make up
sleep 90
make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/valid_pdf_doc.json | python -m json.tool
docker compose -f docker-compose.yml exec web pytest -v
uv run ruff check .
uv run ruff format --check .
```

## Verdict
One paragraph. Is Phase 5a ready? Is Phase 5b unblocked?
```

---

## What "done" looks like for this prompt

Output to chat:

1. Path to `implementation_report.md`.
2. Overall status: PASS / FAIL / PARTIAL.
3. Acceptance criteria score: X/10.
4. PointStruct id finding (one line).
5. Phase 1+2+3+4 regression status.
6. Recommended next step (Phase 5b unblocked?).

Then **stop**.

---

## A note on honesty

If Qdrant rejects the chunk_id format, say so and document the workaround. If the embedder cold-load timed out a request, say so. If a test is flaky due to lock-release timing, flag it. The report is the contract.
