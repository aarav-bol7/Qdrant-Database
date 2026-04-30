# Phase 5a Plan Review

## Summary

- **Total findings:** 17
- **Severity breakdown:** 3 critical · 8 major · 6 minor
- **Plan accuracy:** 16/16 hard constraints addressed; 10/10 acceptance criteria mapped; 10/10 spec pitfalls covered; one DRF behavior (default-exception-handler bypass) was implicit in R11 but not promoted to a top-level test.
- **Recommendation:** **accept revised plan** — all critical/major findings have non-controversial inline fixes. Nothing escalated.

---

## Findings by lens

### Lens 1 — Spec compliance

1. **[critical] DRF default exception handler bypasses `{"error": {...}}` shape.** Spec §"API contract" requires the error envelope on EVERY non-201 response. The plan's R11 mentions wrapping unhandled exceptions, but there's no top-level test verifying the envelope on a forced 500 (e.g., simulated Qdrant outage). Fix: add an explicit Step 7 view-level guard (outer `try/except Exception` returning 500 with `{"error":{"code":"internal_error","message":...}}`) AND a `tests/test_upload.py` test that asserts shape on a forced unexpected exception. Cross-references finding #11.

2. **[critical] Step 8 (urls.py + config/urls.py) must commit atomically.** Plan calls this out, but the verification "between" the two edits could leave the project in a `manage.py check` failure state. Fix: emit BOTH edits in a single tool-call group; only run `manage.py check` AFTER both are applied. Already implicit in revised plan, made explicit in §3.

3. **[critical] Bot.get_or_create race not handled in code.** Plan §4 R15 acknowledges the race; the spec's pitfall #7 calls for `IntegrityError + refetch`. The original plan's Step 6 didn't include retry-on-IntegrityError code-pattern guidance. Fix: revised pipeline.py snippet wraps the `get_or_create` calls in a small `_get_or_create_with_retry` helper that catches `IntegrityError`, calls `objects.get(...)`, and continues. Same for Tenant.

### Lens 2 — Edge cases the plan missed

4. **[major] Logging on success AND failure paths.** Spec implies structured INFO logs but plan didn't specify the exact set of fields. Fix: revised Step 7 lists the required INFO log keys: `tenant_id, bot_id, doc_id, items, chunks, status_code, elapsed_ms, status` (created/replaced/failed). On failure, use `logger.error(..., exc_info=True)`.

5. **[major] No test for embedder failure path.** Pipeline raises `EmbedderError(500)` if `embed_passages` raises. Plan listed this in §4 R but no dedicated test in §3. Fix: revised Step 10 adds `test_500_when_embedder_raises` using `unittest.mock.patch` on `apps.ingestion.pipeline.embed_passages`.

6. **[major] No test for Qdrant write failure.** Same shape as #5. Fix: revised Step 10 adds `test_500_when_qdrant_upsert_raises` patching `apps.qdrant_core.client.get_qdrant_client().upsert`.

7. **[major] Pre-warm requirement documented but not encoded.** Spec hints; plan §6 #10 mentions. Fix: revised plan §3 Step 12 ALWAYS runs `verify_setup.py --full` BEFORE the curl smoke (when stack is up), as the canonical sequence. Documented as the runbook step for production deploys too.

8. **[major] DRF `validate()` and JSON-only assumption.** §6 #2 documented but no test verifies form-data input is rejected/handled. Phase 5a is JSON-only; revised plan adds a sentence in views.py requirements: "If `request.content_type` is not `application/json`, reject with 400 OR rely on DRF's content negotiation" — defer to DRF defaults; document.

9. **[major] Bot.save() FK auto-population vs Phase 2 E006 rename.** Spec §"Hard constraints" #6 says use `bot_ref`. Plan §3 Step 6 lists this. Fix: revised plan adds verification: `Document.objects.filter(bot_ref=bot)` in test_auto_creates_tenant_and_bot to confirm the FK is set (not just the denormalized `bot_id`).

10. **[major] Empty sparse_dict edge case.** Plan §6 #5 covers; spec pitfall doesn't include this directly. Fix: revised plan §4 R adds a clear branch: if FlagEmbedding emits `{}` for a chunk, `sparse_to_qdrant({})` returns `{indices:[],values:[]}`. Test the upsert via the live `verify_setup.py --full` path which uses non-empty sparse, then add a Phase 5b test for empty case.

11. **[major] DRF default exception handler — explicit test for envelope shape.** Same finding as #1, viewed from a different angle. Plan now includes the explicit test.

### Lens 3 — Production-readiness gaps

12. **[minor] Concurrency budget callout.** R12 mentioned 7-GB minimum. Revised plan §4 R12 explicitly states: 2 gunicorn workers × ~1.8 GB embedder + ~120 MB transient ColBERT per request → ~4 GB; plus Postgres + Redis + Qdrant overhead → ~7 GB minimum host RAM.

13. **[minor] No pipeline-level timeout guard.** §4 R13. Phase 5b can add explicit `time.monotonic()` budget check; plan flags as v1-acceptable.

14. **[minor] HTTP_PORT=8080 vs 8000.** Phase 1 used 8000 in spec; Makefile uses HTTP_PORT defaulting to 8080. Revised plan §3 Step 12 uses `localhost:8080` (matches Makefile + .env).

### Lens 4 — Pitfall coverage audit

| # | Spec pitfall | Plan covers? | Verification? |
|---|---|---|---|
| 1 | `Document.bot` vs `bot_ref` | ✓ §3 Step 6 | ✓ test_auto_creates_tenant_and_bot |
| 2 | `config/urls.py` namespace collision | ✓ §3 Step 8 | ✓ Phase 1 healthz regression check |
| 3 | DRF ChoiceField + source_type mismatch | ✓ §3 Step 5 | ✓ test_unsupported_source_type_returns_400 |
| 4 | `UUIDField(required=False)` returns UUID, not str | ✓ §3 Step 7 | ✓ pipeline takes `str(doc_id)` |
| 5 | `Tenant.get_or_create` defaults | ✓ §3 Step 6 | ✓ pipeline passes `defaults={"name":...}` |
| 6 | `Bot.save()` auto-populates collection_name | ✓ §3 Step 6 (no `collection_name=` in get_or_create) | ✓ test_auto_creates verifies collection_name |
| 7 | Tenant.get_or_create race | ✓ §4 R2 + R15; revised: explicit IntegrityError-retry code | ✓ Phase 5b adds concurrency test |
| 8 | Embedder cold load | ✓ §4 R3, §3 Step 12 (verify_setup --full warmup) | ✓ make health + curl after warmup |
| 9 | PointStruct id format | ✓ §4 R1, §3 Step 2 canary | ✓ Step 2 prints accept/reject |
| 10 | Test pollution | ✓ §4 R5, fresh_bot fixture | ✓ pytest tests/test_upload.py twice |

### Lens 5 — Sequencing

15. **[minor] Step 4 (locks.py) verification asserts Postgres reachable.** Phase 5a tests the lock context manager indirectly through pipeline tests. Direct test: deferred to Phase 5b's `tests/test_locks.py`. Plan §4 R8 acknowledges.

### Lens 6 — Verification quality

Strong overall. One enhancement:

16. **[minor] V11 (curl smoke) needs explicit grep for `status: "replaced"`.** Original plan had `python -m json.tool` only. Revised: pipe through `python -c "import json,sys; d=json.load(sys.stdin); print(d['status']); assert d['status'] in ('created','replaced')"`.

### Lens 7 — Tooling correctness

17. **[minor] `<str:>` URL converter accepts forward-slash-stripped strings.** Slash already strips by Django's URL parser; encoded `%2F` doesn't reach the converter unless `APPEND_SLASH=False` AND middleware lets it through. Phase 2's slug regex catches anything weird. No code change needed; plan §4 R16 documents.

---

## Findings escalated to user

**None.** All 17 findings have non-controversial inline fixes. The one judgment call (cross-tenant `doc_id` collision → 500 vs 409) is documented as a §6 ambiguity but stays at 500 to match spec literally; if the user later wants 409, that's a small subclass change in `apps/documents/exceptions.py`.

---

## Recommendation

**Ready for Prompt 3 (implementation).** Revised plan.md addresses every critical/major finding inline. Minor findings are clarity improvements.
