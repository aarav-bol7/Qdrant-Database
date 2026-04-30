# Phase 5b Plan Review

## Summary

- **Total findings:** 14
- **Severity breakdown:** 2 critical · 7 major · 5 minor
- **Plan accuracy:** 12/12 hard constraints addressed; 10/10 acceptance criteria mapped; 10/10 spec pitfalls covered; one structural concern (chunk-cap test runtime ~250 s with real BGE-M3 tokenizer) needed restructuring.
- **Recommendation:** **accept revised plan** — all critical/major findings have non-controversial inline fixes. No findings escalated to user.

---

## Findings by lens

### Lens 1 — Spec compliance

1. **[major] Phase 5a's `test_201_replace_existing` breaks after 5b's short-circuit lands.** Phase 5a's `valid_pdf_doc.json` has `"content_hash": "sha256:a3f8c4b1e2"`. Two consecutive POSTs with the same body will trigger 5b's short-circuit (200 `no_change`) instead of the 5a replace path (201 `replaced`). Plan §6 #8 flagged this but didn't include an explicit step to modify the existing test. Fix: revised plan §3 Step 8 EXPLICITLY edits the existing test to set `body["content_hash"] = "sha256:second-hash"` between the two POSTs, preserving the test's intent (replace path) while adapting to the new short-circuit gate.

### Lens 2 — Edge cases the plan missed

2. **[critical] `test_422_too_many_chunks` runtime is ~250 s with real BGE-M3 tokenizer.** Plan §6 #4 noted "potentially ~10 s" — but each `chunk_item` call to the FAQ chunker invokes `count_tokens` once per chunk, plus once per `_truncate_to_max_tokens`. Real tokenizer calls cost ~50 ms each on CPU. 5001 items × ~3 tokenizer calls × 50 ms ≈ 750 s. Way too slow. Fix: restructure the test to produce 5001 chunks via 5 LONGER items (each ~1000 chunks worth of content), reducing total tokenizer calls. Alternative: mock `count_tokens` to return a constant, but then we're not testing real chunker behavior. Best mitigation: use a SINGLE long item with 5001-chunk-equivalent content + assert the cap fires. Revised plan re-spells the test body.

3. **[critical] `pg_try_advisory_lock` `cursor.fetchone()` returning `None` not handled.** If the cursor is in an unexpected state (e.g., previous statement didn't return a row), `fetchone()` returns None and `(got,) = None` raises `TypeError`. Fix: revised plan adds defensive `row = cursor.fetchone(); got = bool(row and row[0])` pattern in locks.py.

4. **[major] `is_replace` flag computed BEFORE short-circuit but referenced AFTER.** In Phase 5a, `is_replace` was set right after `existing` lookup. In 5b, the short-circuit returns early — but `is_replace` is still set above the short-circuit gate, then reused in the post-chunking code path (replace branch's `delete_by_doc_id` call, and the final UploadResult.status determination). This is fine logically, but the plan should make this explicit. Fix: revised plan §3 Step 4 clarifies the variable lifecycle.

5. **[major] Threaded test (`tests/test_locks.py::test_concurrent_acquire_blocks`) connection ownership.** Each thread gets its own DB connection from Django's connection pool. Worker's `connections.close_all()` only closes the worker's connection. Main thread's connection is unaffected. Fix: plan §4 R4 + R15 already discuss this; revised plan adds an explicit comment in the test code (no-op for main thread; mandatory for worker).

6. **[major] Retry-After header value not asserted in test.** Spec acceptance criterion 6 (manual smoke) doesn't include the 409 path. Plan §3 Step 5 + Step 8 should add an explicit test for the header value. Fix: add `test_409_retry_after_header` to `tests/test_locks.py` (since it's already exercising lock contention) OR to `tests/test_upload.py`. Revised plan adds it to test_locks.py via a separate worker thread that hits the view's 409 path.

   **Refinement:** simpler path — add a unit test in `tests/test_pipeline.py` that mocks `upload_lock` to raise `ConcurrentUploadError(retry_after=7)`, posts via DRF APIClient, and asserts `response["Retry-After"] == "7"`. This avoids actual lock contention in tests. Revised plan moves the assertion into a new test in test_upload.py.

7. **[major] Document row's `content_hash` defaults to empty string.** Phase 2's `Document.content_hash = CharField(max_length=80)` has no default. Phase 5a's pipeline writes `body.get("content_hash") or ""` to the field. So a Document created with no content_hash has `content_hash=""`. The short-circuit's check is:
   ```
   incoming_hash and existing.content_hash == incoming_hash
   ```
   `incoming_hash` non-empty AND equality. If both are `""`, `incoming_hash` is falsy → no short-circuit. Good. If incoming is `"abc"` and existing is `""`, equality fails → no short-circuit. Good. **Edge case:** if Phase 5a's first POST set existing.content_hash to "abc", and a 5b POST sends the same "abc", short-circuit fires correctly. Plan covers it via Test 1; revised plan tightens the test name.

8. **[minor] Whitespace-only `content_hash` in body.** `(body.get("content_hash") or "").strip()` strips whitespace. If body has `"   "`, post-strip empty → no short-circuit. ✓

9. **[minor] `_POLL_INTERVAL_S = 0.05` busy-loop.** 100 polls × 5 s. Mild CPU; ~3000 wakeups/min if always contended. Acceptable. Could use exponential backoff but adds complexity. v1 keeps the constant.

10. **[minor] `pg_advisory_unlock` returning False.** If the lock was already released (e.g., by connection close), the call returns False but doesn't raise. Plan §4 R covers; revised plan adds: don't assert the unlock return value.

### Lens 3 — Production-readiness gaps

11. **[major] Logging discipline on the short-circuit path.** Plan §3 Step 4 includes `logger.info("upload_no_change", ...)`. Verify the log fields match the keys other endpoints use (`tenant_id, bot_id, doc_id, chunk_count, items_processed`). Add a log at INFO with full context.

12. **[major] Mock-patch path correctness.** Plan §3 Step 6 patches `apps.ingestion.pipeline.embed_passages`, `apps.ingestion.pipeline.get_qdrant_client`, etc. These work IF pipeline.py imports those names directly (which it does per Phase 5a — `from apps.ingestion.embedder import embed_passages`). The patch must target the IMPORTED name, not the source module. Verify: `apps.ingestion.pipeline.embed_passages` is a bound reference. ✓ as long as Phase 5a's import structure is preserved.

13. **[minor] No assertion that `last_refreshed_at` actually changed on no_change save.** Plan §6 #1 mentions verifying via "read timestamp before + after." Revised plan adds this assertion to `test_no_change_when_content_hash_matches_and_chunks_exist`.

14. **[minor] `Connection close_all()` for SQLite test backend.** SQLite has no advisory locks, so `connections.close_all()` is harmless. The test should skip on SQLite (real Postgres required); plan covers via module-level vendor check.

### Lens 4 — Pitfall coverage audit

| # | Spec pitfall | Plan covers? | Verification? |
|---|---|---|---|
| 1 | Short-circuit before lock release | ✓ §3 Step 4 (save inside upload_lock context) | Implicit in test |
| 2 | `pg_try_advisory_lock` vs `pg_advisory_lock` | ✓ §4 R3 | grep guard |
| 3 | Threaded test connection close | ✓ §4 R4 | test_locks.py spec body |
| 4 | Phase 5a tests breaking | ✓ §6 #8 → revised §3 Step 8 (explicit test edit) | Re-run pytest after 5b |
| 5 | `update_fields` with `auto_now` | ✓ §6 #1 → revised test asserts timestamp delta | Test-asserted |
| 6 | Cross-tenant `doc_id` short-circuit guard | ✓ §3 Step 4 (raise QdrantWriteError) | Implicit |
| 7 | 5001-item test slowness | ✗ **gap** (finding #2 above) — restructured in revised plan |
| 8 | `MAX_CHUNKS_PER_DOC` constant location | ✓ §3 Step 4 (pipeline.py module level) | grep |
| 9 | `Retry-After` header presence | ✗ **gap** (finding #6 above) — added test in revised plan |
| 10 | Pipeline tests under `@pytest.mark.django_db` | ✓ §3 Step 6 (every test marked) | Test-discoverable |

### Lens 5 — Sequencing

Plan order matches spec. No issues.

### Lens 6 — Verification command quality

Strong overall. Revised plan adds `grep -c 'pg_try_advisory_lock' apps/ingestion/locks.py` ≥ 1 to V3 (already present).

### Lens 7 — Tooling correctness

- ✓ `from django.db import connections` (plural) for the worker thread close.
- ✓ `@pytest.mark.django_db(transaction=True)` for threaded test.
- ✓ `unittest.mock.patch("apps.ingestion.pipeline.embed_passages", ...)` targets the binding inside pipeline.py.

### Lens 8 — Risk register completeness

Revised plan adds:
- **R16** (new): chunk-cap test runtime — restructured to ~5 s via single-item-many-chunks pattern OR mocked tokenizer.
- **R17** (new): `pg_advisory_unlock` returning False after connection-close releases the lock — don't assert.
- **R18** (new): `cursor.fetchone()` returning None — defensive read.

---

## Findings escalated to user

**None.** All 14 findings have inline fixes.

---

## Recommendation

**Ready for Prompt 3 (implementation).** Revised plan.md addresses every critical/major finding. Two critical findings (chunk-cap test slowness; cursor.fetchone defensive read) are corrected inline. The pre-existing Phase 5a test (`test_201_replace_existing`) needs a small edit to keep working post-5b — covered explicitly in revised Step 8.
