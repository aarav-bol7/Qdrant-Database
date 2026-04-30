# Phase 6 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_6_delete_api/spec.md` — source of truth.
2. `build_prompts/phase_6_delete_api/plan.md` — to critique.
3. `build_prompts/phase_5b_upload_idempotency/spec.md` — Phase 5b's lock contract.
4. `build_prompts/phase_5b_upload_idempotency/implementation_report.md` — Phase 5b outcomes.
5. `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a contract.
6. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3's `delete_by_doc_id`.
7. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
8. `README.md` — context.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_6_delete_api/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_6_delete_api/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 5 deliverables addressed? (4 modifications + 1 new test)
- All 12 hard constraints addressed?
- All 10 acceptance criteria mapped to steps?
- All 10 common pitfalls in risk register?
- Out-of-scope respected?

### Lens 2 — Edge cases the plan missed

- **Lock acquired BEFORE the Document lookup.** Phase 6's pipeline acquires the lock, THEN looks up the Document. If the Document doesn't exist, we still hold the lock for ~5ms. Acceptable, but worth noting. Alternative: lookup first, then lock. But that's racy (a concurrent upload could create the doc between lookup and lock-and-act). Plan should explicitly justify the lock-then-lookup order.
- **Soft-deleted Document re-uploaded by Phase 5.** If a doc is DELETEd (status="deleted"), then Phase 5 receives a POST with the same doc_id, what happens? Phase 5a's pipeline does `Document.objects.filter(doc_id=...).first()` — finds the soft-deleted row, `is_replace=True`. It calls `delete_by_doc_id` (no-op, already gone), upserts new chunks, updates Document.status to "active". Re-activation works! Plan should note this is INTENTIONAL.
- **`Document.objects.get(doc_id=...)` race.** Two concurrent DELETEs serialize via the lock. The first acquires, deletes, releases. The second acquires, sees status="deleted" (`was_already_deleted=True`), no-ops, returns 204. ✓
- **`Document.last_refreshed_at` doesn't update via `update_fields=["status", ...]` if the field isn't in the list.** Phase 6 explicitly includes `"last_refreshed_at"` in update_fields. Verify Django's `auto_now=True` triggers when the field is explicitly listed.
- **`drop_collection` in test cleanup might fail if collection doesn't exist.** The fixture's `try/except` swallows. Verify.
- **`response.content == b""` may not be exactly empty in some DRF versions.** Some versions return `b''` for 204; others return None. Test should use `not response.content` or len check.
- **`uploaded_doc` fixture creates Tenant + Bot + Document via Phase 5a's pipeline.** That pipeline triggers BGE-M3 load. First test run: ~30s. Subsequent tests in the same session: fast. Plan should note the cold-start cost.
- **Test `test_404_cross_tenant_doc_id` creates a second auto-tenant on DELETE.** WAIT — Phase 6 spec says DELETE does NOT auto-create. So how does `tenant_b` exist? Re-reading the test: tenant_b doesn't exist; the DELETE finds NO Document with that doc_id in tenant_b → 404. The test passes. But the test's docstring should clarify: tenant_b never gets a Document row. The cleanup `drop_collection(tenant_b, bot_b)` is defensive (it shouldn't have been created either). Plan should clarify this.
- **`Document.objects.filter(doc_id=doc_id, status__in=[ACTIVE, PENDING, FAILED]).first()`?** That'd hide soft-deleted docs from the lookup, returning 404 on already-deleted. But we WANT idempotent re-delete to return 204. So the lookup must include status="deleted" rows. Phase 6's spec uses `.filter(doc_id=doc_id).first()` (no status filter) — correct. Plan should explicitly verify.
- **Phase 6 doesn't add an audit-log entry.** Audit log is v3. But the structured `delete_succeeded` log line at INFO captures the same info. Plan should note this is the v1 trail.
- **`<uuid:doc_id>` URL converter rejects uppercase UUIDs?** Django's converter is case-insensitive for UUID format, but the resulting `uuid.UUID` object is canonical (lowercase). Postgres UUID column comparison is case-insensitive too. Plan can confirm via a test.

### Lens 3 — Production-readiness gaps

- **No DELETE-vs-UPLOAD ordering guarantee at the API layer.** A client racing DELETE + POST for the same doc_id gets undefined ordering — Phase 5b's lock serializes, but the OUTCOME depends on which acquires first. Acceptable behavior; clients shouldn't race their own deletes.
- **No bulk DELETE.** Single-doc only. Bulk delete is v5. Plan should confirm not in scope.
- **`response[Retry-After] = str(exc.retry_after)`.** DRF's Response inherits Django's HttpResponse, which supports `[]` setitem for headers. Verify.
- **404 doesn't reveal `tenant_id`/`bot_id` to caller.** Spec says response body for 404 is `{"error": {"code": "document_not_found", "message"}}`. The message text shouldn't leak that the tenant exists but the doc doesn't (vs. tenant doesn't exist at all). Generic message: `"Document {doc_id} not found"`. Plan should specify.
- **Logging format.** Phase 5b's pipeline logs `delete_succeeded` (or `upload_succeeded`) at INFO. Phase 6 should match the structured format (`tenant_id`, `bot_id`, `doc_id`, `chunks_deleted`, `was_already_deleted`, `elapsed_ms`).
- **No timeout on `delete_by_doc_id`.** Phase 3's `delete_by_doc_id` uses `qdrant-client`'s default timeout (10s). For a doc with thousands of chunks, that should be plenty. v1 acceptable.

### Lens 4 — Pitfall coverage audit

For all 10 spec.md pitfalls.

### Lens 5 — Sequencing & dependency correctness

- exceptions.py extension first.
- pipeline.py extension second (uses DocumentNotFoundError).
- views.py extension third (uses DeletePipeline).
- urls.py extension fourth (uses DeleteDocumentView).
- Tests last.

### Lens 6 — Verification command quality

- Manual curl smoke covers 201 → 204 → 204 → 404 → 400 in a single block. Strong.
- pytest tests/test_delete.py — strong (covers ORM + Qdrant state).
- Full suite + ruff + manage.py check — strong.

### Lens 7 — Tooling correctness

- DRF `Response(status=204)` body: empty.
- DRF `Response.headers` mutation via `[]` setitem.
- Django `<uuid:>` URL converter.
- pytest `@pytest.mark.django_db` for Document model writes.

### Lens 8 — Risk register completeness

- `Document.update_or_create`-style race during DELETE? Not applicable — DELETE only touches an existing row, never creates.
- `delete_by_doc_id` edge case: what if Qdrant returns a partial delete count (some chunks deleted, others not)? Phase 3's helper presumably uses Qdrant's atomic filter delete — should be all-or-nothing. Verify.
- Concurrent tests in pytest — pytest-django default is sequential (one test at a time per worker). Plan can ignore parallelism.

---

## Output structure

### File 1: `plan_review.md` (NEW)

Standard structure with sections per lens, summary, recommendation.

### File 2: `plan.md` (OVERWRITE)

Same 10-section structure. Add section 0: **"Revision notes"** linking to plan_review.md finding numbers. Resolve all [critical] and [major].

---

## What "done" looks like

Output to chat:

1. Both files saved.
2. Severity breakdown.
3. Findings escalated.
4. Recommendation.

Then **stop**.
