# Phase 5b — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_5b_upload_idempotency/spec.md` — source of truth.
2. `build_prompts/phase_5b_upload_idempotency/plan.md` — to critique.
3. `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a contract.
4. `build_prompts/phase_5a_upload_core/implementation_report.md` — Phase 5a outcomes.
5. `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract.
6. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract.
7. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
8. `README.md` — context.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_5b_upload_idempotency/plan_review.md` — critique findings.
- `build_prompts/phase_5b_upload_idempotency/plan.md` — overwritten with revised plan.

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 5 modified files addressed?
- All 12 hard constraints?
- All 10 acceptance criteria mapped?
- All 10 common pitfalls in risk register?
- Out-of-scope respected?

### Lens 2 — Edge cases the plan missed

- **Short-circuit when content_hash is provided in body but Document row has empty content_hash.** Does this still trigger the short-circuit? Spec says only when `existing.content_hash == incoming_hash` AND both non-empty. Verify the AND-non-empty check is present.
- **Body has whitespace-only content_hash.** The `(body.get("content_hash") or "").strip()` pattern in spec handles it. Verify.
- **`pg_try_advisory_lock` returns false vs database error.** Function returns false when lock is held; raises if database connection broken. `(got,) = cursor.fetchone()` — what if `got` is None? Plan should handle.
- **The `_POLL_INTERVAL_S = 0.05` busy-loop.** Wakes 100x in 5s. Mild CPU. Could use `time.sleep(1.0)` for fewer wakeups. Trade-off: longer wait time on lock release.
- **Lock release inside `try/finally` BUT the lock might already be released by `pg_try_advisory_unlock` failing.** Implement: `cursor.execute("SELECT pg_advisory_unlock(...)")` returns boolean. Don't assert it returned true (might already be released by connection close).
- **`test_concurrent_acquire_blocks` thread doesn't share Django settings with main thread.** `pytest-django` uses thread-local DB connections; threads need their own connection. Plan should verify.
- **Phase 5a's `is_replace` flag is set BEFORE the short-circuit check.** After 5b adds the short-circuit, `is_replace` is computed but unused if we short-circuit. Plan should keep `is_replace` AFTER the short-circuit check OR reorganize.
- **Document.update_or_create uses `defaults={"content_hash": ...}`.** When the body has empty content_hash, the Document row's content_hash is updated to "". Subsequent uploads with empty content_hash → existing.content_hash is "" → short-circuit if `existing.content_hash == incoming_hash` AND `existing.content_hash != ""`. The short-circuit's `incoming_hash` empty-check correctly skips. Verify with a test.
- **Concurrent test slowness.** `pg_try_advisory_lock` polling at 50ms interval × 5s timeout = 100 polls. Acceptable.
- **Worker thread close_all() is needed but `connections.close_all()` only closes the CURRENT thread's connection.** Each thread's connection is independent. Plan should confirm `close_all()` in the thread (not main).
- **Chunk cap test runtime.** 5001 items × tokenizer call per item. With BGE-M3 tokenizer warm, ~50ms per item × 5001 = ~250s. WAY too slow. Plan should restructure: produce 5001 chunks via FEWER, LONGER items so the tokenizer is called less. Or: mock the tokenizer for this specific test.

### Lens 3 — Production-readiness gaps

- **`Retry-After` header is HTTP/1.1 standard.** DRF / Django's Response sets headers via `response[key] = value`. Verify DRF doesn't strip custom headers.
- **5s lock timeout may be too short on a busy system.** Two simultaneous large uploads of the same doc_id: first takes 30s, second times out at 5s. Better than blocking 60s. Acceptable v1.
- **No metric for `concurrent_upload` errors.** Phase 8 will add Prometheus; plan should log at WARNING with structured fields so the log can be greppable.
- **Embedder mock in test_pipeline.py.** Tests patch `apps.ingestion.pipeline.embed_passages` — but the embedder is imported AT MODULE LOAD time. `unittest.mock.patch` rebinds at test-runtime. Verify the patch path matches the actual import.
- **`existing.last_refreshed_at` is auto_now.** When we call `existing.save(update_fields=["last_refreshed_at"])`, Django's auto_now updates the field. Without the explicit save, the field doesn't change. Test asserts the field changed.
- **No test verifies the `Retry-After` header value matches `retry_after` from the exception.** Add an assertion.

### Lens 4 — Pitfall coverage audit

For all 10 spec.md pitfalls.

### Lens 5 — Sequencing & dependency correctness

- exceptions.py extension first.
- locks.py modification (uses ConcurrentUploadError) second.
- pipeline.py modification third.
- views.py modification fourth.
- Tests last.

### Lens 6 — Verification command quality

- pytest tests/test_pipeline.py — strong (mocked embedder, fast).
- pytest tests/test_locks.py — strong (real Postgres, catches lock semantics issues).
- Manual curl smoke — strong (proves end-to-end short-circuit + chunk cap + 409 retry).

### Lens 7 — Tooling correctness

- `connections.close_all()` import path: `from django.db import connections`.
- `@pytest.mark.django_db(transaction=True)` for tests using threading; the default django_db wraps each test in a transaction that doesn't propagate to other threads.

### Lens 8 — Risk register completeness

Add anything missed.

---

## Output structure

Standard `plan_review.md` + overwritten `plan.md` with revision notes.

---

## What "done" looks like

Output to chat:

1. Both files saved.
2. Severity breakdown.
3. Findings escalated.
4. Recommendation.

Then **stop**.
