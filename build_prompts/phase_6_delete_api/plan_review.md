# Phase 6 Plan Review

## Summary

- **Total findings:** 12
- **Severity breakdown:** 1 critical · 6 major · 5 minor
- **Plan accuracy:** 12/12 hard constraints addressed; 10/10 acceptance criteria mapped; 10/10 spec pitfalls covered; one structural concern (DRF `Response(status=204).content` portability) needed an explicit assertion form.
- **Recommendation:** **accept revised plan** — all findings have inline fixes. No findings escalated.

---

## Findings by lens

### Lens 1 — Spec compliance

1. **[major] `Document.objects.filter(...)` lookup must NOT exclude soft-deleted rows.** For idempotent re-delete to return 204, the lookup must include `status="deleted"` rows. Plan §6 #9 anticipated this; revised plan adds explicit confirmation that the spec body's `Document.objects.filter(doc_id=doc_id).first()` (no status filter) is correct, and `was_already_deleted` is computed AFTER the lookup.

### Lens 2 — Edge cases the plan missed

2. **[critical] `response.content == b""` may not be portable across DRF versions.** Some versions return `b''` for 204; others return `None` or `b'null'` if the response was rendered before being returned. Fix: revised `test_204_delete_existing_doc` uses `assert not response.content` (Pythonic falsy-check) instead of strict equality.

3. **[major] Lock acquired BEFORE Document lookup — needs explicit justification.** Spec body acquires `upload_lock` first, then looks up the Document. Even if the doc doesn't exist, the lock is held for ~5 ms while we acquire+release. Alternative (lookup first) is racy: a concurrent upload could create the doc between lookup and lock-and-act. Plan §4 R covers; revised plan adds explicit comment in §3 Step 3 explaining the lock-first ordering is correct.

4. **[major] Soft-deleted Document re-uploaded by Phase 5 = re-activation.** If a doc is DELETEd then POSTed with the same doc_id, Phase 5a's pipeline finds the soft-deleted row (`is_replace=True`), calls `delete_by_doc_id` (no-op), upserts new chunks, updates `Document.status` to ACTIVE. **This is intentional re-activation behavior.** Revised plan §6 ambiguities adds this as confirmed v1 behavior.

5. **[major] `test_404_cross_tenant_doc_id` doesn't auto-create tenant_b.** Plan body shows the test creates tenant_a, posts a doc, then DELETEs the same doc_id from tenant_b. Phase 6 spec says DELETE does NOT auto-create. So tenant_b never gets a Tenant row; the lookup returns no Document → 404. The cleanup `drop_collection(tenant_b, bot_b)` is defensive (no collection should exist). Revised test docstring clarifies.

6. **[major] 404 error message should not leak tenant existence.** If the tenant doesn't exist vs. if the tenant exists but no doc, the message should be the same: `"Document {doc_id} not found."` — generic. Spec body matches this. Revised plan §6 ambiguities confirms.

7. **[minor] `<uuid:doc_id>` accepts uppercase UUIDs.** Django's URL converter is case-insensitive for UUID format. The view receives `uuid.UUID` object (canonical lowercase). DB lookup is canonical. v1 acceptable; no special handling needed. Revised plan documents.

### Lens 3 — Production-readiness gaps

8. **[major] Logging discipline on success and failure paths.** Spec body has `logger.info("delete_succeeded", extra={...})` in the pipeline. View should also emit a final INFO log at end of `delete()` with `tenant_id, bot_id, doc_id, status_code=204, elapsed_ms`. Mirrors Phase 5a's view pattern. Revised plan §3 Step 4 explicit.

9. **[minor] `response["Retry-After"] = str(exc.retry_after)`.** Phase 5b proven; DRF Response inherits HttpResponse's setitem. Revised plan keeps as-is.

10. **[minor] No timeout on `delete_by_doc_id`.** Phase 3's helper uses qdrant-client's `timeout=10` from Phase 3's client wrapper. For thousands of chunks per doc, 10 s should be plenty. v1 acceptable.

### Lens 4 — Pitfall coverage audit

| # | Spec pitfall | Plan covers? |
|---|---|---|
| 1 | `<uuid:doc_id>` not `<str:doc_id>` | ✓ §3 Step 5 |
| 2 | Filter by doc_id only, then check tenant/bot | ✓ §3 Step 3, §4 R1 |
| 3 | 204 with body | ✓ §4 R13; revised test uses `not response.content` |
| 4 | Lock key matches upload | ✓ §3 Step 3 |
| 5 | `update_fields` includes `last_refreshed_at` | ✓ §3 Step 3, §4 R4 |
| 6 | `chunks_deleted` from helper, not hard-coded | ✓ §3 Step 3 (uses return value) |
| 7 | Reusing `ConcurrentUploadError` for delete | ✓ §3 Step 4, §4 R3 |
| 8 | Embedder cold load on `uploaded_doc` fixture | ✓ §4 R9 |
| 9 | `test_404_malformed_uuid` asserts status only | ✓ §3 Step 7 |
| 10 | `test_qdrant_chunks_gone` cleanup via drop_collection | ✓ §3 Step 7 (fresh_bot fixture) |

### Lens 5 — Sequencing

Plan order matches spec dependencies. No issues.

### Lens 6 — Verification quality

Strong overall. Revised plan strengthens the 204-content assertion to handle DRF version variation.

### Lens 7 — Tooling correctness

- ✓ `<uuid:doc_id>` URL converter.
- ✓ `Response(status=204)` no body.
- ✓ DRF `response[key] = value` for headers.
- ✓ `@pytest.mark.django_db` on tests touching ORM.
- ✓ autouse `_bypass_pg_advisory_lock_for_sqlite_tests` fixture (Phase 5a pattern).

### Lens 8 — Risk register completeness

11. **[minor] `delete_by_doc_id` partial-delete behavior.** Phase 3's helper uses Qdrant's filter-based delete which is atomic — all-or-nothing per doc_id filter. v1 accepts the qdrant-client guarantee.

12. **[minor] pytest sequential by default.** Plan §4 R8 covers fixture pollution via `uuid.hex[:8]` per-test slugs. pytest-django runs sequentially unless `-n` is specified; plan §6 ambiguity #19 (Phase 5b finding) advised against `-n` for embedder tests, same applies here.

---

## Findings escalated to user

**None.** All 12 findings have inline fixes.

---

## Recommendation

**Ready for Prompt 3 (implementation).** Revised plan.md adds the explicit handling for the critical finding (DRF 204 body portability) and tightens the major findings around lock ordering, soft-delete re-activation, cross-tenant test docstring, and logging discipline. The 5 minor findings are clarity improvements.
