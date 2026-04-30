# Phase 5b — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not modify any file. Do not run uv sync.**

---

## Required reading (in this order)

1. `README.md` — context.
2. `build_prompts/phase_5b_upload_idempotency/spec.md` — full Phase 5b spec. **Source of truth. Read twice.**
3. `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a contract; Phase 5b extends 5a's `pipeline.py` and `locks.py`.
4. `build_prompts/phase_5a_upload_core/implementation_report.md` — confirms 5a's deliverables, especially the PointStruct id format choice.
5. `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract.
6. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract.
7. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.

If `phase_5b_upload_idempotency/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_5b_upload_idempotency/plan.md
```

---

## What the plan must contain

### 1. Plan summary

3–5 sentences. What's being added? What's the riskiest part? How does the build verify itself?

### 2. Build order & dependency graph

Phase 5b modifies 4 files and adds 2 test files. Order:

- exceptions.py extension (just two new classes — no deps)
- locks.py modification (uses ConcurrentUploadError → must come after exceptions extension)
- pipeline.py modification (uses ConcurrentUploadError implicitly via locks.py + new DocumentTooLargeError)
- views.py minor extend (status code + Retry-After header)
- test_pipeline.py (mocks embedder; depends on pipeline structure)
- test_locks.py (real Postgres; depends on locks.py)
- test_upload.py extension (depends on view + pipeline)

### 3. Build steps (sequenced)

8–12 numbered steps. Each: goal · files · verification · rollback.

### 4. Risk register

- **Short-circuit accidentally runs full pipeline.** Bug in the if-condition: e.g., `existing.content_hash == incoming_hash` must compare equal AND non-empty. Test mock-based.
- **`update_fields=["last_refreshed_at"]` doesn't trigger auto_now.** Verify with a unit test.
- **`pg_try_advisory_lock` semantics differ from `pg_advisory_lock`.** First returns true/false immediately; second blocks. Misusing the wrong one: pipeline hangs (block) instead of returning 409.
- **Threaded test for concurrent locks doesn't close DB connection.** Lock leaks; subsequent tests fail.
- **`MAX_CHUNKS_PER_DOC = 5000` is too low for legitimate large docs.** Acceptable for v1; flag for v2 review.
- **Order of new checks.** Short-circuit must happen BEFORE `get_or_create_collection` — otherwise we create a Qdrant collection unnecessarily on every no-op.
- **DRF doesn't return Response with custom HTTP status code on no_change.** Verify the view distinguishes `status_code = 200 if result.status == "no_change" else 201`.
- **Phase 5a tests fail after Phase 5b changes.** The `test_201_replace_existing` test in 5a doesn't use a content_hash, so 5b's short-circuit shouldn't trigger. Verify.
- **`Tenant.get_or_create` happens BEFORE the short-circuit check.** That means a no-op upload still creates Tenant/Bot rows on first contact. Acceptable design — caller wouldn't call to a tenant that doesn't exist except via auto-create.
- **Concurrent lock test takes too long.** 1s timeout × handful of test → ~5s total. Acceptable.

### 5. Verification checkpoints

- exceptions.py: import smoke shows new classes.
- locks.py: import smoke + standalone test of acquire/release.
- pipeline.py: `manage.py check` passes.
- views.py: `manage.py check`.
- test_pipeline.py: `pytest tests/test_pipeline.py -v` green (no Qdrant needed since fully mocked).
- test_locks.py: `pytest tests/test_locks.py -v` green or skipped (real Postgres needed).
- test_upload.py extensions: `pytest tests/test_upload.py -v` green INCLUDING all 5a tests.
- Manual curl smoke: 200 no_change second time, 422 on cap-exceeding doc.
- Phase 5a regression: `pytest tests/test_upload.py -v` shows all 5a tests still present and green.
- Full suite: `pytest -v` from container.

### 6. Spec ambiguities & open questions

- **`update_fields=["last_refreshed_at"]` and `auto_now`.** Per Django docs, `auto_now` updates on every save() including with `update_fields` if the field is in the list. Confirm.
- **`save(update_fields=["last_refreshed_at"])` doesn't update `chunk_count`.** That's intentional — short-circuit doesn't change chunk count. But will Django's `auto_now` only update if the field is in update_fields? Yes — and we explicitly include it. ✓
- **`ConcurrentUploadError` propagation through `with upload_lock(...)`.** The lock context manager raises the error BEFORE entering the `with` block. So `try/except` outside the `with` catches it. Plan should verify the structure works.
- **5001-item test for chunk cap is slow.** Maybe reduce to 5001 items but each only producing 1 chunk would still take a few seconds. Acceptable.
- **`test_locks.py` uses `connections.close_all()`** to release the worker's DB connection. Without this, the lock leaks for ~60s (CONN_MAX_AGE).
- **`Retry-After` HTTP header in DRF.** Django's HttpResponse supports `response["Retry-After"] = "5"`. DRF's Response inherits — verify.

### 7. Files deliberately NOT created / NOT modified

- All Phase 5a files except `pipeline.py`, `locks.py`, `exceptions.py`, `views.py`, `test_upload.py` are unchanged.
- All Phase 1/2/3/4 files unchanged.
- No new fixtures (reuse 5a's).

### 8. Acceptance-criteria mapping

For all 10: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
uv run pytest tests/test_pipeline.py -v
uv run pytest tests/test_locks.py -v
uv run pytest tests/test_upload.py -v
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
make health

# Inside container
docker compose -f docker-compose.yml exec web pytest -v

# Smoke
DOC_ID=$(uuidgen)
# (see spec.md for the full sed pattern)
```

### 10. Estimated effort

Per step.

---

## Output format

Single markdown file at `build_prompts/phase_5b_upload_idempotency/plan.md`. 300–550 lines (smaller scope than 5a).

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Line count.
3. 5-bullet summary.
4. Spec ambiguities (titles).

Then **stop**.
