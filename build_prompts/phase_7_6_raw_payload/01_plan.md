# Phase 7.6 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **PLAN, not code. Do not modify any file.**

---

## Required reading (in this order)

1. `README.md` — project charter.
2. `build_prompts/phase_7_6_raw_payload/spec.md` — full Phase 7.6 spec. **Source of truth. Read twice.**
3. `build_prompts/phase_7_5_api_cleanup/implementation_report.md` — Phase 7.5 outcomes. Note the cross-doc_id dedup logic added there; this phase coexists with it.
4. `build_prompts/phase_5b_upload_idempotency/spec.md` — content_hash short-circuit + advisory lock; `no_change` semantics defined here.
5. `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's upload contract; unchanged.
6. `apps/documents/models.py` — current `Document` model.
7. `apps/documents/admin.py` — current admin config.
8. `apps/ingestion/pipeline.py` — current pipeline; note the three return paths (no_change × 2, full upload) and where `update_or_create` is called.
9. `tests/test_pipeline.py` — current pipeline tests; note the `TestContentHashShortCircuit` class structure and the `_body()`, `_doc_id()`, `mock_embedder` fixtures.

If `phase_7_6_raw_payload/spec.md` does not exist, abort.

---

## Your task

Produce a structured plan. Save to:

```
build_prompts/phase_7_6_raw_payload/plan.md
```

---

## What the plan must contain

### 1. Plan summary

3–5 sentences. What's being added (the new column + admin + tests)? What's the riskiest part (migration timing, dedup-path semantics)? How does the build verify itself?

### 2. Build order & dependency graph

Phase 7.6 modifies 3 files + adds 1 migration + edits 1 test file. Order:

- `apps/documents/models.py` first (independent of code; trigger migration step).
- `make makemigrations-host APP=documents` — generate `0002_document_raw_payload.py` on host (uses test_settings overlay; no DB connection needed).
- Inspect the generated migration — must be exactly one `AddField` op; no unrelated changes pulled in.
- `apps/ingestion/pipeline.py` — add `"raw_payload": body` to the create/replace branch's `update_or_create` defaults. Do NOT touch the two no_change branches.
- `apps/documents/admin.py` — add the pretty-print callable, update `readonly_fields`, add `exclude = ("raw_payload",)`.
- `tests/test_pipeline.py` — add `TestRawPayloadPersistence` class with 3 tests.
- Stack rebuild + smoke after all source changes.

### 3. Build steps (sequenced)

8–10 numbered steps. Each: goal · files · verification · rollback.

Critical sequencing:
- Edit `models.py` BEFORE running `makemigrations-host` — otherwise the generated migration is empty.
- The migration file must be COMMITTED on host (visible in `apps/documents/migrations/`) BEFORE `make rebuild` — the new image bakes in the file, then the container's startup `migrate --noinput` applies it.
- `pipeline.py` and `admin.py` edits can happen in any order relative to each other, but BOTH must precede the rebuild.
- Tests added LAST (after all source changes settle), so test runs verify the final state.

### 4. Risk register

Reference the spec.md "Common pitfalls" section. The plan must address each one with a concrete preventative step. Especially:

- **`raw_payload` accidentally written on no_change branches.** Plan must explicitly call out the THREE return paths in `UploadPipeline.execute()` and state which one writes `raw_payload`.
- **Migration applied inside container only.** Plan must verify the migration FILE exists on host before rebuild.
- **Generated migration captures unrelated model drift.** Plan must include an inspection step on the migration file before commit.
- **Admin form double-renders `raw_payload`.** Plan must verify both `exclude = ("raw_payload",)` AND `raw_payload_pretty` in `readonly_fields`, never `raw_payload` directly in `readonly_fields`.
- **Test fixture pollution from cross-doc_id dedup.** Plan must verify each test uses unique content (different `content` strings) OR runs on a fresh tenant/bot pair to avoid Phase 7.5's content-hash dedup short-circuiting test 3.
- **`ensure_ascii=False` and `format_html` both required.** Plan must include both in the admin pretty-print callable spec.

### 5. Verification checkpoints

7–10 with exact commands and expected outcomes:

- After `models.py` edit: `python manage.py check` (inside container or via `make run`) clean.
- After `makemigrations-host`: file `apps/documents/migrations/0002_document_raw_payload.py` exists; contains exactly one `AddField` operation; no unrelated ops.
- After `pipeline.py` edit: `make run python manage.py check` clean.
- After `admin.py` edit: `make run python manage.py check` clean; `make run python -c "from apps.documents.admin import DocumentAdmin; assert 'raw_payload' in DocumentAdmin.exclude"`.
- Stack rebuild: `make rebuild && make health` green.
- Migration applied: `make run python manage.py showmigrations documents` shows `[X] 0002_document_raw_payload`.
- Manual curl smoke: upload a body → admin shows JSON; re-upload same → `no_change`, JSON unchanged; re-upload different content same doc_id → `replaced`, JSON updated.
- Tests in container: `make run pytest tests/test_pipeline.py -v` all green.
- Phase 1-7.5 regression: `make run pytest -v` keeps all prior tests green.
- Migration reversibility: `make run python manage.py migrate documents 0001_initial` (reverse) then `make run python manage.py migrate` (re-apply) — both succeed.

### 6. Spec ambiguities & open questions

3-5 entries:

- **`raw_payload` value when DRF defaults are applied.** Spec says "validated body" which means DRF has already applied `source_type="text"` default if caller omitted. Confirm the test asserts on the validated body (post-DRF defaults), not the raw input.
- **Test ordering and fixture state.** Tests 2 and 3 depend on test 1's state if they share a DB. Confirm test isolation strategy (each test creates its own Document with unique `content` OR uses a fresh `_doc_id()`).
- **Admin display when `raw_payload` is `{}` (empty dict).** Should that render as `{}` or `—`? Plan should pick a position — likely render `{}` (truthy-by-Python-standards but distinct from None).
- **Migration filename.** `makemigrations` defaults to `0002_document_raw_payload.py` based on the field name; plan should confirm this is the expected name in any verification commands.
- **Raw payload size in Postgres.** No size limit in spec but practical Postgres jsonb ceiling is ~100MB per row. Plan should note this for Phase 8 awareness; no enforcement in this phase.

### 7. Files deliberately NOT created / NOT modified

Echo spec.md's "Out of scope" + the don't-touch list (everything not in the 5-file modification set: `models.py`, `admin.py`, `pipeline.py`, the new migration file, `tests/test_pipeline.py`).

### 8. Acceptance-criteria mapping

For all 11 acceptance criteria from spec.md: which step satisfies, which command verifies, expected output.

### 9. Tooling commands cheat-sheet

```
# Migration
make makemigrations-host APP=documents
make run python manage.py showmigrations documents
make run python manage.py migrate

# Standard
make run python manage.py check
make run pytest -v
make run pytest tests/test_pipeline.py::TestRawPayloadPersistence -v

# Stack
make rebuild
make health
make ps

# Manual smoke (assumes test_t/test_b already exists)
DOC=$(curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
  -H "Content-Type: application/json" \
  -d '{"items":[{"content":"raw payload smoke 1"}]}' | python3 -c "import sys,json; print(json.load(sys.stdin)['doc_id'])")
echo "doc_id: $DOC"

# Re-upload same → no_change
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
  -H "Content-Type: application/json" \
  -d '{"items":[{"content":"raw payload smoke 1"}]}'
echo

# Open admin: http://localhost:8080/admin/documents/document/<doc_id>/change/
# Verify "Raw payload (uploaded JSON)" shows the JSON.
```

### 10. Estimated effort

Per step. Phase 7.6 is small: ~1-2 hours of agent work, ~20-40 lines of net new source code + 1 migration + ~50-80 lines of new tests.

---

## Output format

Single markdown file at `build_prompts/phase_7_6_raw_payload/plan.md`. 200–400 lines.

---

## What "done" looks like

Output to chat:

1. `plan.md` created.
2. Total line count.
3. 3-bullet summary of key sequencing decisions (especially: how migration generation, pipeline edit, and admin edit are sequenced; how tests verify the no_change-vs-replace branches).
4. Spec ambiguities flagged in section 6 (titles only).

Then **stop**.
