# Phase 5a — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to PLAN, not to write code. Do not create source files. Do not modify any Phase 1/2/3/4 file (except plan to modify `config/urls.py`).**

---

## Required reading (in this order)

1. `README.md` — project charter.
2. `build_prompts/phase_5a_upload_core/spec.md` — the full Phase 5a specification. **Source of truth. Read it twice.**
3. `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract. Phase 5a consumes the embedder/chunker/payload public API surface.
4. `build_prompts/phase_4_embedding_chunking/implementation_report.md` — confirms the `devices=[...]` API and the public surface.
5. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract; `get_or_create_collection`, `delete_by_doc_id`, `get_qdrant_client`.
6. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract; `Tenant`, `Bot`, `Document` (note: `bot_ref` not `bot`), `slug_validator`, `validate_slug`, `advisory_lock_key`.
7. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract; `config/urls.py` is the only Phase 1 file modified.

If `phase_5a_upload_core/spec.md` does not exist, abort.

---

## Your task

Produce a structured implementation plan. Save to:

```
build_prompts/phase_5a_upload_core/plan.md
```

---

## What the plan must contain

### 1. Plan summary

3–5 sentence executive summary at the top (write last). What's getting built? What's the riskiest part? How will the build verify itself?

### 2. Build order & dependency graph

Enumerate every file from spec.md's "Deliverables" tree (8 changed files + 3 fixtures). Dependencies:

- exceptions.py first (no deps)
- locks.py next (depends on Phase 2's `advisory_lock_key` + Django connection)
- pipeline.py next (depends on Phase 2/3/4 APIs + locks.py + exceptions.py)
- serializers.py independent (DRF only)
- views.py depends on serializers + pipeline + exceptions + Phase 2's `validate_slug`
- urls.py depends on views
- config/urls.py modification depends on apps/documents/urls.py existing
- Tests depend on everything above

### 3. Build steps (sequenced)

10–14 numbered steps. Each step:
- **Goal** (one sentence)
- **Files touched**
- **Verification command**
- **Rollback action**

Critical sequencing:
- Verify the FlagEmbedding `devices=[...]` API is still working from Phase 4 before any test that loads the embedder.
- `apps/documents/urls.py` AND `config/urls.py` modification are committed TOGETHER (otherwise the URL routing breaks).
- Stack rebuild + manual curl smoke comes BEFORE the pytest run.

### 4. Risk register

Cover at minimum:

- **PointStruct `id` field requires UUID format (not arbitrary strings).** Phase 4's `chunk_id` is `f"{doc_id}__i{item_index}__c{chunk_index}"` — NOT a UUID. Qdrant may reject this. Plan must include "verify acceptable id formats with installed qdrant-client" before writing the upsert call. Mitigation options: (a) use chunk_id directly if accepted; (b) wrap with hash → uuid; (c) use chunk_id as payload field and let Qdrant auto-assign UUIDs.
- **`Tenant.objects.get_or_create` race** between two concurrent uploads to a new tenant. Catch IntegrityError and refetch.
- **Embedder cold-load timeout.** First request after stack restart → ~30s model load → 60s gunicorn timeout may be tight. Mitigation: `verify_setup.py --full` warmup before testing.
- **`config/urls.py` modification breaks Phase 1's `/healthz`.** The `path("", include("apps.core.urls"))` must remain. The new `path("v1/", ...)` is added, not replaced.
- **Test pollution between runs.** Tests that share `(tenant_id, bot_id)` collide. Always use `uuid.uuid4().hex[:8]` per test.
- **DRF serializer rejecting `tenant_id` in body.** The validate() method needs to inspect `self.initial_data`, not `attrs` (which DRF strips unknown keys from by default unless using ModelSerializer). Verify which dict to inspect.
- **Pipeline transaction boundaries.** `with transaction.atomic()` wraps Document.update_or_create — but Tenant/Bot get_or_create runs OUTSIDE the atomic block. If Document update fails, Tenant/Bot stay created. Acceptable in v1 (auto-create is desired).
- **Advisory lock release on exception.** The `with upload_lock(...)` context manager must release in `finally`. Verify Python's context manager protocol handles the exit correctly.
- **PostgreSQL connection used by `connection.cursor()` for advisory lock.** This is the same connection Django ORM uses. The lock is session-level, so it auto-releases when the connection is returned to the pool. Make sure CONN_MAX_AGE doesn't cause confusion.
- **Phase 1/2/3/4 regression** from `config/urls.py` modification. Test by running `make health` AFTER the change.

### 5. Verification checkpoints

8–12 with exact commands and expected outcomes:

- After exceptions.py: import smoke.
- After locks.py: import smoke + isolated test of `upload_lock` context manager (acquire / release).
- After pipeline.py: import smoke + `manage.py check`.
- After serializers.py: import smoke + manual test of `UploadBodySerializer({}).is_valid()` returning False.
- After views.py: `manage.py check`.
- After urls.py + config/urls.py: `manage.py show_urls` (if django-extensions installed) OR `python -c "from django.urls import reverse; print(reverse('upload-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'}))"`.
- After fixtures: confirm valid JSON via `python -m json.tool tests/fixtures/valid_pdf_doc.json`.
- After test_upload.py: skip-not-fail if Qdrant unreachable on host; run inside container if needed.
- After stack rebuild: `make down && make up && sleep 90 && make health`.
- Manual curl smoke: 201 fresh + 201 replace + 400 bad slug.
- Phase 1+2+3+4 regression: `uv run pytest -v` (excluding Phase 5a-only embedder-dependent tests if model not on host).

### 6. Spec ambiguities & open questions

5–10 entries. Things to scrutinize:

- **PointStruct id format.** Verify with `from qdrant_client.models import PointStruct; PointStruct(id="any-string", vector={...}, payload={...})` — does it accept any string, or only UUID/int? If it rejects, choose the workaround.
- **DRF `validate()` access to forbidden keys.** When `self.initial_data` is a dict (which it is for JSON requests), `set(self.initial_data.keys())` works. For form data it might be a QueryDict. We're JSON-only, so fine, but document.
- **Embedder availability on host.** `tests/test_upload.py` runs from the host shell which doesn't have BGE-M3 cached. Tests that load the embedder will pay 30s+ on first run OR skip if no network. The session fixture should distinguish.
- **`@pytest.mark.django_db` on test_upload.py.** Tests touch the ORM via the pipeline. Required.
- **Sparse vector with empty `lexical_weights`.** If a chunk's text is "the" (all stopwords), FlagEmbedding might return `{}`. `sparse_to_qdrant({})` returns `{indices: [], values: []}`. Does Qdrant accept this in `SparseVector`? Verify.
- **Tenant + Bot creation outside the upload_lock.** If two uploads to two different doc_ids in the same new tenant race, both call `get_or_create(tenant_id)`. Race resolved by IntegrityError + retry.
- **`Document.objects.update_or_create` with composite-FK relationship.** Phase 2's Document model has `bot_ref` (FK to Bot, surrogate auto-PK) AND denormalized tenant_id/bot_id CharFields. `update_or_create(doc_id=...)` uses doc_id as the lookup; defaults dict sets bot_ref + tenant_id + bot_id. Verify the FK is set correctly.
- **`connection.cursor()` inside a context manager.** If Django's connection is in autocommit mode (default), the advisory lock is session-level. If a transaction is open, the lock is still session-level (advisory locks don't tie to transactions unless using `pg_advisory_xact_lock`). Phase 5a uses session-level — verify.
- **The `existing.tenant_id != tenant_id` check.** If existing Document has a different tenant_id, that's a UUID collision OR a real bug. Phase 5a raises QdrantWriteError → 500. Is this the right code? Maybe 409 conflict is cleaner. Spec says 500. Make a call.

### 7. Files deliberately NOT created / NOT modified

Echo spec.md's "Out of scope" + the don't-touch list (every Phase 1/2/3/4 file except `config/urls.py`).

### 8. Acceptance-criteria mapping

For all 10 criteria: which build step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Stack
make up
make down
make health

# From host
uv run pytest tests/test_upload.py -v
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run

# Inside container (when needed)
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full

# Manual smoke
curl -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/valid_pdf_doc.json | python -m json.tool
```

### 10. Estimated effort

Per-step wallclock. Note that the embedder load on first test run is ~30-60s.

---

## Output format

Single markdown file at `build_prompts/phase_5a_upload_core/plan.md`. 400–700 lines.

---

## What "done" looks like for this prompt

Output to chat:

1. Confirmation `plan.md` was created.
2. Total line count.
3. 5-bullet summary of key sequencing decisions.
4. Spec ambiguities flagged in section 6 (titles only).

Then **stop**. No code yet.
