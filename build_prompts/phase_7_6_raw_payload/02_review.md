# Phase 7.6 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_7_6_raw_payload/spec.md` — source of truth.
2. `build_prompts/phase_7_6_raw_payload/plan.md` — to critique.
3. `build_prompts/phase_7_5_api_cleanup/spec.md` — Phase 7.5's contract; Phase 7.6 must not regress it.
4. `build_prompts/phase_7_5_api_cleanup/implementation_report.md` — Phase 7.5 outcomes (cross-doc_id content_hash dedup).
5. `build_prompts/phase_5b_upload_idempotency/spec.md` — `no_change` semantics.
6. `apps/documents/models.py`, `apps/documents/admin.py`, `apps/ingestion/pipeline.py`, `tests/test_pipeline.py` — current state.
7. `README.md` — context.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_7_6_raw_payload/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_7_6_raw_payload/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 5 modified files addressed?
- All 12 hard constraints addressed (especially: dedup branches NOT touching raw_payload, migration must be reversible, admin must use `format_html` + `ensure_ascii=False`)?
- All 11 acceptance criteria mapped to steps?
- All 10 common pitfalls in risk register?
- Out-of-scope respected?

### Lens 2 — Edge cases the plan missed

- **`raw_payload` and `body` mutation.** If anything in the pipeline mutates `body` (e.g., adds default keys, normalizes values) AFTER it's been written to `update_or_create` defaults, the persisted JSON could differ from what's in memory at the time of write. Plan should verify `body` is not mutated after the dedup checks return.
- **`update_or_create` "create" vs "update" defaults semantics.** When `update_or_create(doc_id=X, defaults={...})` finds an existing row, it UPDATES with the defaults dict. So `raw_payload=body` overwrites the existing one. ✓ for replace path. But: if a future code path called `update_or_create` *without* the dedup short-circuit running first (e.g., a refactor), it could accidentally clobber. Plan should note this is robust against the current dedup paths but call out the assumption.
- **DRF default application timing.** When the upload view validates via `UploadBodySerializer`, `validated_data` has DRF defaults applied (`source_type="text"` if omitted, `section_path=[]`, etc.). Plan should confirm `body` passed to `UploadPipeline.execute()` is `serializer.validated_data` (not `request.data` raw), since the spec says "persist the validated body."
- **Admin pretty-print on a payload with non-ASCII.** Plan should test: upload with `content` containing Cyrillic, CJK, emojis. The admin should render them as actual characters (because `ensure_ascii=False`), not as `\uXXXX` escapes.
- **`raw_payload` retained on a failed upload.** If the embedder raises mid-pipeline (after `raw_payload` would have been written, before `update_or_create` runs), no Document row is saved. So `raw_payload` is never written for failed uploads. Plan should confirm this is the desired behavior (vs. writing a "Failed" row with raw_payload for forensics — currently OUT of scope per spec, but worth flagging).
- **Postgres `jsonb` indexing.** No GIN index in scope, but plan should note that ad-hoc admin search on `raw_payload` will be a sequential scan. Acceptable for v1; flag for Phase 8 if search-in-payload becomes a need.
- **Migration name when run repeatedly.** If `makemigrations` is run a second time after the first migration is committed, it produces no new file (correctly). Plan should include this as a self-check ("rerunning makemigrations produces zero new files").
- **Test isolation when run with pytest-django's transactional reset.** `@pytest.mark.django_db` rolls back per test. So even if test 1's body and test 2's body have identical content, the rollback prevents cross-doc_id dedup from firing across tests. Plan should confirm this is the case for the chosen test class.

### Lens 3 — Production-readiness gaps

- **Postgres `jsonb` storage limits.** Postgres TOAST silently handles values up to ~1GB but practical row hygiene caps at ~100MB. Plan should note for awareness; no enforcement.
- **Admin page render time.** A 50MB `raw_payload` pretty-printed inline is ~50MB of HTML in the response. Plan should note this could lag the admin page load; the `max-height: 600px; overflow: auto` CSS limits the *display* but not the response payload size. v1 acceptable; Phase 8 could add a "show raw" / "show 1KB preview" toggle.
- **Logging the raw payload.** If existing logs already include `body` or extras that overlap with raw_payload, we'd duplicate. Plan should verify no log line contains `raw_payload` content (the existing log lines use small `extra` dicts with metadata only — confirm).
- **`raw_payload` exposure in API responses.** The HTTP search response and the gRPC Chunk message do NOT include `raw_payload` — search returns chunk-level data only. Plan must verify no view or serializer leaks `raw_payload` to non-admin callers.
- **Backward compatibility for existing `Document` rows.** All have `raw_payload=NULL` after migration. Old admin queries / model methods must still work. Plan must verify `Document.objects.all()` and `.filter(...)` are unaffected.

### Lens 4 — Pitfall coverage audit

For all 10 spec.md pitfalls. Plan must address each.

### Lens 5 — Sequencing & dependency correctness

Critical sequence:
- `models.py` BEFORE `makemigrations-host` (otherwise empty migration).
- Migration file inspection BEFORE `make rebuild` (catch unrelated drift).
- Migration committed on host BEFORE `make rebuild` (image bakes file from host context).
- Pipeline + admin edits independent of each other; both BEFORE rebuild.
- Tests added LAST.
- Stack rebuild AFTER all source changes.

### Lens 6 — Verification command quality

- After `models.py` edit: model introspection one-liner — strong.
- After `makemigrations-host`: file existence check + content inspection — strong.
- After `pipeline.py` + `admin.py`: `manage.py check` import smoke — strong.
- After rebuild: `showmigrations` confirming `[X]` — strong.
- Manual curl smoke covering create + no_change + replaced — strong.
- pytest test_pipeline.py covering all three branches — strong.
- Phase 1-7.5 regression via full suite — strong.
- Migration reversibility test — should be in plan.

### Lens 7 — Tooling correctness

- `make makemigrations-host APP=documents` uses `tests.test_settings` (SQLite overlay) — confirmed working in past phases.
- `make run python manage.py ...` is the new uv-like wrapper added during post-7.5 polish; verify the plan uses it (and that the SKIP_AS_RUN_ARG guard prevents `make run python manage.py migrate` from triggering the existing `make migrate` target).
- `make run pytest ...` for in-container test runs.

### Lens 8 — Risk register completeness

- **Existing Phase 7.5 tests** — none touch `raw_payload`, but plan should grep for any reference and note none exist.
- **Phase 6 delete tests** — `test_delete.py` shouldn't care about `raw_payload`. Plan must verify no test asserts on a Document's full field set in a way that would break.
- **Migration ordering vs Phase 7.5's existing migrations** — if Phase 7.5 added a migration (it didn't, per the implementation report — only schema-irrelevant changes), the new migration's parent should be Phase 5/6's last migration (likely `0001_initial`). Plan must verify the dependency chain.

---

## Output structure

### File 1: `plan_review.md` (NEW)

Standard structure with sections per lens, summary, recommendation.

### File 2: `plan.md` (OVERWRITE)

Same structure as the original. Add section 0: **"Revision notes"** linking to plan_review.md finding numbers. Resolve all [critical] and [major] findings inline.

---

## What "done" looks like

Output to chat:

1. Both files saved.
2. Severity breakdown.
3. Findings escalated.
4. Recommendation.

Then **stop**.
