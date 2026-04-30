# Phase 5b — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **BUILD per the revised plan, VERIFY against the spec, REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_5b_upload_idempotency/spec.md` — re-read.
2. `build_prompts/phase_5b_upload_idempotency/plan.md` — revised plan.
3. `build_prompts/phase_5b_upload_idempotency/plan_review.md` — critique.
4. `build_prompts/phase_5a_upload_core/spec.md` + implementation_report.md — Phase 5a contract.
5. `build_prompts/phase_4_embedding_chunking/spec.md` + report — Phase 4.
6. `build_prompts/phase_3_qdrant_layer/spec.md` + report — Phase 3.
7. `build_prompts/phase_2_domain_models/spec.md` + report — Phase 2.
8. `build_prompts/phase_1_foundation/spec.md` — Phase 1.

If any required input is missing, abort.

---

## Hard rules

1. Follow the revised plan. Document deviations.
2. Build in plan order.
3. Run verification at every checkpoint.
4. Honor "Out of scope" — no DELETE endpoint, no atomic version swap, no search.
5. Modify ONLY the explicitly-extended files: `apps/ingestion/{pipeline,locks}.py`, `apps/documents/{exceptions,views}.py`, `tests/test_upload.py`.
6. Add new files: `tests/test_pipeline.py`, `tests/test_locks.py`, the implementation report.
7. NO modification to any other Phase 1/2/3/4/5a file.
8. No code comments unless spec/invariant justifies.
9. Never commit `.env`.
10. No emoji. No `*.md` beyond `implementation_report.md`.

---

## Implementation phases

### Phase A — Extend exceptions.py

Add `ConcurrentUploadError` and `DocumentTooLargeError` to `apps/documents/exceptions.py`. Don't modify the existing `UploadError`, `InvalidPayloadError`, `NoEmbeddableContentError`, `QdrantWriteError`, `EmbedderError` classes.

**Verify:**
```bash
uv run python -c "
from apps.documents.exceptions import (
    UploadError, InvalidPayloadError, NoEmbeddableContentError,
    QdrantWriteError, EmbedderError,
    ConcurrentUploadError, DocumentTooLargeError,
)
print('imports ok')

# Verify new classes carry expected attrs
e = ConcurrentUploadError('test', retry_after=5)
assert e.http_status == 409 and e.code == 'concurrent_upload' and e.retry_after == 5
e2 = DocumentTooLargeError('test', details={'chunk_count': 10000, 'max': 5000})
assert e2.http_status == 422 and e2.code == 'too_many_chunks'
print('attrs ok')
"
```

### Phase B — Modify locks.py

Replace the `pg_advisory_lock` (blocking) with `pg_try_advisory_lock` + retry loop + 5s timeout. Add `timeout_s` kwarg defaulting to `DEFAULT_ACQUIRE_TIMEOUT_S = 5.0`.

**Verify:**
```bash
make up   # if not already up
sleep 60
uv run python -c "
from apps.ingestion.locks import upload_lock, DEFAULT_ACQUIRE_TIMEOUT_S
import time
print(f'timeout default: {DEFAULT_ACQUIRE_TIMEOUT_S}')

started = time.monotonic()
with upload_lock('test_t', 'test_b', 'doc-acquire'):
    print(f'acquired in {time.monotonic() - started:.3f}s')
print('released')
"
```

### Phase C — Modify pipeline.py

Add the short-circuit logic + chunk cap. Insert into the existing `UploadPipeline.execute()` method:

1. **After** Tenant + Bot get_or_create, **before** `get_or_create_collection`: the content-hash short-circuit branch.
2. **After** chunking all items into `flat`, **before** embedding: the chunk-cap check.

Also: when computing `collection_name` for the no_change path, import from `apps.qdrant_core.naming` (don't call `get_or_create_collection` — that creates the collection unnecessarily).

**Verify:**
```bash
uv run python manage.py check                                # 0 issues
uv run python -c "
from apps.ingestion.pipeline import UploadPipeline
print(UploadPipeline)
"
```

### Phase D — Modify views.py

Two minor changes:
1. Distinguish 200 vs 201 in success response: `status_code = 200 if result.status == 'no_change' else 201`.
2. For `ConcurrentUploadError`, set `Retry-After` header on the response.

**Verify:**
```bash
uv run python manage.py check
```

### Phase E — Pipeline tests (mocked embedder)

Create `tests/test_pipeline.py` per spec. Uses `unittest.mock.patch` to mock `embed_passages`, `get_qdrant_client`, `get_or_create_collection`, `delete_by_doc_id`. Fast: <2s per test.

**Verify:**
```bash
uv run pytest tests/test_pipeline.py -v
```

### Phase F — Lock tests (real Postgres)

Create `tests/test_locks.py` per spec. Uses threading to test concurrent acquisition. Each thread's connection must be closed in finally to avoid lock leaks.

**Verify:**
```bash
uv run pytest tests/test_locks.py -v
```

If Postgres is unreachable, tests skip gracefully (covered by Phase 5a's session fixture pattern OR the tests use `@pytest.mark.django_db(transaction=True)` which already requires a real DB — pytest-django will fail-fast if it can't connect, which is fine).

### Phase G — Extend test_upload.py

Add the three new tests to the existing `tests/test_upload.py` (DON'T overwrite — append):

1. `test_200_content_hash_short_circuit`
2. `test_422_too_many_chunks`
3. `test_409_concurrent_upload` — IF you can simulate it cleanly (may require threading in the test); if not, skip and document.

**Verify (inside container, where embedder is cached):**
```bash
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v
```

All Phase 5a tests still in the output AND all Phase 5b additions are green.

### Phase H — Stack-level smoke

```bash
make down
make up
sleep 90
make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full   # warm embedder

# Test 1: 201 fresh + 200 no_change
DOC_ID=$(uuidgen)
sed "s/^{/{\"doc_id\":\"$DOC_ID\",\"content_hash\":\"sha256:fixed\",/" \
    tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json

curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\nHTTP %{http_code}\n"  # 201

curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\nHTTP %{http_code}\n"  # 200 no_change

# Test 2: 422 chunk cap exceeded
python -c "
import json
items = [{'item_index': i, 'content': 'Question? Answer text.'} for i in range(5001)]
print(json.dumps({'source_type': 'faq', 'items': items}))
" > /tmp/big-doc.json

curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" -d @/tmp/big-doc.json -w "\nHTTP %{http_code}\n"  # 422
```

Expected:
1. First POST → 201 + status: created
2. Second POST → 200 + status: no_change
3. Cap-exceeding POST → 422 + code: too_many_chunks

### Phase I — Full suite + regression

```bash
docker compose -f docker-compose.yml exec web pytest -v
uv run pytest -v                                              # host (skips embedder-loading tests)
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run

# Phase 1+2+3+4+5a regression
make health                                                   # green
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v   # 5a + 5b all green

git status --short                                            # ONLY Phase 5b extensions + new tests
```

Files in `git status --short` must be:
- `apps/ingestion/pipeline.py` (modified)
- `apps/ingestion/locks.py` (modified)
- `apps/documents/exceptions.py` (extended)
- `apps/documents/views.py` (minor extend)
- `tests/test_pipeline.py` (new)
- `tests/test_locks.py` (new)
- `tests/test_upload.py` (extended)
- `build_prompts/phase_5b_upload_idempotency/implementation_report.md` (new)

Anything else is a deviation requiring justification.

---

## Self-review

After Phase I passes, run self-review against the **spec**.

For each acceptance criterion (10): pass/fail, command, output.
For each pitfall (10): avoided/hit/N/A.
For each "Out of scope" item: confirmed not implemented.

---

## Final report

Save to `build_prompts/phase_5b_upload_idempotency/implementation_report.md`. Same structure as prior phases:

```markdown
# Phase 5b — Implementation Report

## Status
**OVERALL:** PASS / FAIL / PARTIAL

## Summary
- Files modified: <N> (must be ≤ 4: pipeline, locks, exceptions, views)
- Files created: <N> (must be 3: test_pipeline, test_locks, implementation_report)
- Tests added: <N>
- Acceptance criteria passing: <N>/10

## Acceptance criteria
[for all 10]

## Pitfall avoidance
[for all 10]

## Out-of-scope confirmation
[brief list]

## Phase 1+2+3+4+5a regression
- All prior tests still green: <output>
- /healthz still green: <output>
- 5a's test_upload.py tests still appear in output and pass: <list>
- No prior-phase file modified: paste git diff --name-only

## Deviations from plan
[for each]

## Spec defects discovered
[anything in spec.md that turned out incorrect]

## Outstanding issues
[non-blocking]

## Files modified or created
[clean tree]

## Commands to verify the build (one block)
```bash
make down && make up && sleep 90 && make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
docker compose -f docker-compose.yml exec web pytest -v
uv run ruff check . && uv run ruff format --check .
```

## Verdict
One paragraph. Phase 5 complete? Phase 6 unblocked?
```

---

## What "done" looks like

Output to chat:

1. Path to implementation_report.md.
2. Status: PASS / FAIL / PARTIAL.
3. Acceptance criteria score: X/10.
4. Phase 1-5a regression: PASS / FAIL.
5. Recommended next step (Phase 6 unblocked?).

Then **stop**.

---

## Honesty note

If the chunk-cap test takes 5 minutes due to tokenizer calls, say so and propose a faster alternative. If `pg_try_advisory_lock` returns NULL on a fresh connection, say so. The report is the contract.
