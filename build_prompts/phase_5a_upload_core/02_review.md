# Phase 5a — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to CRITIQUE the plan and revise it. Do not write production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_5a_upload_core/spec.md` — source of truth.
2. `build_prompts/phase_5a_upload_core/plan.md` — the plan to critique.
3. `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract.
4. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract.
5. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
6. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.
7. `README.md` — context.

If `plan.md` does not exist, abort: `"Plan not found. Run PROMPT 1 first."`

---

## Your task

Adversarially review the plan. Find every gap, wrong assumption, missed edge case, and production-readiness flaw. Produce a revised plan.

Save outputs:

- `build_prompts/phase_5a_upload_core/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_5a_upload_core/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag each: **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 8 deliverables (4 source + 1 modified + 3 fixtures) addressed?
- All 16 hard constraints addressed?
- All 10 acceptance criteria mapped to steps?
- All 10 common pitfalls in risk register?
- Out-of-scope list respected?

### Lens 2 — Edge cases the plan missed

- **PointStruct id format.** Critical risk; if Qdrant rejects non-UUID strings, the entire upload fails. Plan must include API-inspection step before writing the upsert.
- **`Document.bot_ref` FK assignment.** When calling `Document.objects.update_or_create(doc_id=..., defaults={"bot_ref": bot, ...})`, the FK is set. But `bot_ref_id` (the underlying column) is also a field — does Django auto-populate it from `bot_ref`? Yes, but verify.
- **DRF parses JSON body via `request.data`.** If the request comes as form data instead of JSON, `request.data` is a QueryDict. The serializer's `validate()` does `set(self.initial_data.keys())` — works for QueryDict too. Plan should note the assumption (always JSON in practice).
- **Lock release after process crash.** If a worker dies mid-pipeline, Postgres still holds the advisory lock until the connection times out. CONN_MAX_AGE=60 means up to 60s of stale lock. Acceptable; document.
- **Embedder load on first request blocking the entire request thread.** ~30s. The 60s gunicorn timeout barely covers it. Plan should require `verify_setup.py --full` warmup post-deploy as a documented operational step.
- **Concurrent upload to same `(tenant_id, bot_id)` but different doc_ids.** Different lock keys → both proceed. Both call `get_or_create_collection` → Phase 3's idempotency handles 409. Plan should explicitly note this is FINE in 5a.
- **DRF's `ListField(min_length=1)` vs `validate(empty_items)`.** The serializer's `validate()` checks `not attrs.get("items")` → 400. But what if the body is `{"items": []}`? `attrs["items"]` is `[]` which is falsy. So validate() catches it → 400. ✓.
- **Body field `items[*].url`.** It's a CharField (not URLField) in the spec. Does DRF accept any string? Yes. URLField would reject malformed URLs but that's stricter than v1 needs.
- **Test `test_chunks_have_full_payload_in_qdrant` asserts 20 fields.** If Phase 4's `build_payload` returns only 19 (e.g., `error_message` was missed), the test fails. Plan should map field-count-check to Phase 4's locked schema.
- **Bot.save() race.** If two parallel uploads to same (tenant, bot_id) but DIFFERENT doc_ids: both call `Bot.objects.get_or_create(tenant=t, bot_id=b)`. UniqueConstraint catches the second → IntegrityError → which is NOT caught by `get_or_create`'s typical retry. Need explicit retry-on-IntegrityError.
- **No tests for embedder failure path.** What if `embed_passages` raises mid-pipeline? View should return 500 with `code: "embedder_failed"`. Plan should add a test that mocks embedder to raise.
- **No tests for partial-write Qdrant failure.** Hard to simulate; mock the qdrant client. Plan should add a basic mock-based test.
- **CI should run tests inside container.** Embedder isn't downloaded on host typically. Plan should run `docker compose exec web pytest` for integration tests.

### Lens 3 — Production-readiness gaps

- **Logging structure.** Every request logs at INFO with `tenant_id`, `bot_id`, `doc_id`, `items_count`, `chunks_count`, `elapsed_ms`, `status_code`. Verify the view does this on success AND failure paths.
- **DRF default exception handler.** DRF intercepts unhandled exceptions and returns 500 with a generic body. Phase 5a's view catches `UploadError` explicitly. Other exceptions (e.g., `IntegrityError`, `Exception`) fall through. Plan should ensure the standard 500 response is also wrapped in `{"error": {...}}` format. Easiest: wrap the `pipeline.execute()` call in a try/except Exception → return 500 with `code: "internal_error"`.
- **Concurrency budget.** With 2 gunicorn workers × ~1.8 GB embedder each = 3.6 GB. Adding the upload pipeline's transient ColBERT (~120 MB per request) → ~4 GB. Plus Postgres / Redis / Qdrant. Tight on 8 GB. Plan should flag.
- **Pipeline timeouts.** No internal timeout on `embed_passages` or `client.upsert`. If Qdrant hangs, gunicorn's 60s kills the whole request. The Phase 3 retry decorator + Qdrant client's own `timeout=10` provide some bound. Plan should verify.
- **Slug validation in URL.** Phase 2's `validate_slug` uses regex `^[a-z0-9][a-z0-9_]{2,39}$`. Hyphens fail. The URL `<str:tenant_id>` accepts any non-slash chars. Plan must validate AFTER URL parsing.
- **Pipeline transactional consistency.** If the `Document.update_or_create` fails after Qdrant upsert succeeds, Qdrant has chunks but Postgres doesn't track them. On retry, the pipeline treats it as a fresh upload (Document missing), `delete_by_doc_id` finds nothing to delete (since the doc_id exists in Qdrant but not Postgres — wait no, `delete_by_doc_id` queries Qdrant by filter, so it WILL find the orphan chunks). Actually this is correct behavior. Document. Plan should assert this in a test.
- **`make` targets respect `.env`'s `HTTP_PORT=8080`.** Phase 1's Makefile reads .env. Verify the curl smoke uses 8080.
- **Phase 4's image is ~1.85 GB.** `make up` rebuilds incremental layers. New Phase 5a code is small (mostly Python). No big image growth.

### Lens 4 — Pitfall coverage audit

For all 10 pitfalls in spec.md:
1. Plan addresses?
2. Verification catches?

### Lens 5 — Sequencing & dependency correctness

- exceptions.py before pipeline (pipeline imports from it).
- locks.py before pipeline.
- All Phase 4/3/2 imports verified to exist.
- urls.py + config/urls.py modification atomically (or test 404 between).
- Tests after their modules.

### Lens 6 — Verification command quality

- `manage.py check` — strong (catches URL/import errors).
- Manual curl smoke — strong (proves end-to-end).
- pytest — strong (covers code paths).
- `make health` — Phase 1 regression. Strong.
- Verify chunks in Qdrant via direct client query — strong.

### Lens 7 — Tooling correctness

- `<str:tenant_id>` URL converter not `<slug:>`.
- `path("v1/", include("apps.documents.urls"))` namespace.
- DRF's `APIClient.post(url, body, format="json")` — JSON serialization.
- `@pytest.mark.django_db` for tests touching ORM.

### Lens 8 — Risk register completeness

- Do all the new risks from this review get reflected in the revised plan's risk register?

---

## Output structure

### File 1: `plan_review.md` (NEW)

Standard structure with sections per lens, summary, recommendation.

### File 2: `plan.md` (OVERWRITE)

Same 10-section structure as the original. Add section 0: **"Revision notes"** linking to plan_review.md finding numbers. Resolve all [critical] and [major] findings.

---

## What "done" looks like for this prompt

Output to chat:

1. Confirmation both files saved.
2. Severity breakdown.
3. Findings escalated to user (titles only).
4. Recommendation: ready for Prompt 3, or user must weigh in?

Then **stop**.
