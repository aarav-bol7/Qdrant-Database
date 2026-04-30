# Phase 3 Plan Review

> Adversarial critique of `build_prompts/phase_3_qdrant_layer/plan.md` (revision 1, 425 lines), with the spec re-read fresh.

---

## Summary

- **Total findings:** 16
- **Severity breakdown:** **0 critical**, **1 major**, **15 minor**
- **Plan accuracy vs spec:** ~94%. All deliverables, hard constraints, acceptance criteria, and out-of-scope items are addressed. Major weakness is Step 1's API-inspection battery — it doesn't capture every signature the rest of the build assumes (notably `sparse_vectors_config` parameter name on `create_collection`, `MultiVectorConfig` placement inside `VectorParams` vs separate kwarg).
- **Recommendation:** **Accept revised plan and proceed to Prompt 3.** The two known-likely API drifts (`bot_id="rt"` slug-invalid, `sparse_vectors_config` kwarg name) are mechanical fixes already anticipated; no architectural decisions deferred to user input.

---

## Findings by lens

### Lens 1 — Spec compliance

**L1.1 — [minor] Hard constraint #2 (no new dependencies) not surfaced as a guardrail.**
- Add a one-line risk register entry: "If `uv add` is reached for, you're solving the wrong problem. `qdrant-client` is the only new package surface and it's already pinned in `uv.lock`."
- Verification: `git diff pyproject.toml uv.lock` should show no changes.

**L1.2 — [minor] Hard constraint #12 (no business endpoints) not echoed.**
- Add to plan §3 preamble: "still only `/healthz` and `/admin/`." Belt-and-suspenders for an implementer skimming.

### Lens 2 — Missed edge cases

**L2.1 — [major] Step 1's API inspection battery is incomplete.**
- Missing inspection commands:
  - `inspect.signature(QdrantClient.delete)` — confirms `points_selector=` parameter name (vs `filter=`).
  - `inspect.signature(QdrantClient.upsert)` — confirms `points=` and that the dict-of-vectors form for named vectors (`vector={"dense": [...], "bm25": SparseVector(...), "colbert": [[...]]}`) is accepted.
  - Test that `MultiVectorConfig` is acceptable inside `VectorParams(multivector_config=...)` — some versions require a top-level `multivectors_config={}` arg on `create_collection` instead.
  - Test that `SparseVectorParams(index=SparseIndexParams(on_disk=False), modifier=Modifier.IDF)` is the right shape (vs `index_params=` or `params=`).
  - Confirm the `create_collection` keyword is `sparse_vectors_config` (snake_case, plural) and not `sparse_vectors` or `sparse_config`.
- **Fix:** extend Step 1 with these signature checks and add to Checkpoint A.

**L2.2 — [minor] Test parallelism (`pytest -n N`).**
- Each test creates a uniquely-named collection via `uuid.uuid4().hex[:8]`, so parallel runs don't collide. The session-scoped `qdrant_available` fixture is fine because each pytest-xdist worker forks a fresh process.
- Add to Risk register: "Test parallelism — assumed sequential. If `pytest -n N` is invoked, fixture interaction is untested. Phase 8 may verify."

**L2.3 — [minor] API key rotation invalidates cached client.**
- `settings.QDRANT["API_KEY"]` is read once on first `get_qdrant_client()` call. A subsequent rotation requires a worker restart.
- v1 deferral. Add a one-line risk entry; no fix in Phase 3.

**L2.4 — [minor] `_compare_schema` access pattern (dict vs attribute) not pinned.**
- Plan ambiguity #2 partially covers this for `collection_exists`. Extend to `_compare_schema`'s `info.config.params.vectors["dense"]` — could be `info.config.params.vectors.dense` in some versions.
- Step 1's signature inspection should also `print(type(client.get_collection('any-existing-or-empty-name')).__class__)` — but that requires Qdrant up. Defer to Step 7 manual smoke; document if difference.

**L2.5 — [minor] qdrant-client wraps gRPC errors in its own exception class.**
- `_is_transient` checks `isinstance(exc, grpc.RpcError)` AND a name-based check for `ResponseHandlingException`/`ConnectionError`/`TimeoutException`. Step 1 should inspect `qdrant_client.http.exceptions` and `qdrant_client.grpc` to capture all wrapper types currently in play.
- Low priority; the name-based fallback is defensive.

### Lens 3 — Production-readiness gaps

**L3.1 — [minor] Connection pool / channel reuse.**
- Implicit in the singleton pattern (one `QdrantClient` per worker; gRPC channel reused for all calls). No fix needed; flag in implementation report.

**L3.2 — [minor] Timeout 10s may be too short for Phase 5 large-batch upserts.**
- Phase 3 sets `timeout=10`. Large-batch upserts in Phase 5 may exceed this. Document for Phase 5's review.

**L3.3 — [minor] `drop_collection` has no safety gate.**
- A typo in `tenant_id` could drop the wrong bot's collection, losing data. Phase 8's runbook should require `--confirm` flag in any future CLI wrapper. Note in implementation report.

**L3.4 — [minor] gRPC keepalive not configured.**
- Long-lived clients in Celery workers may hit idle timeout. Phase 8 hardening; not blocking Phase 3.

### Lens 4 — Pitfall coverage audit

All 10 spec pitfalls are covered in the risk register or via specific verification steps. **L4.1 — [minor]:** Pitfall #4 (sparse without IDF) is checked by `_compare_schema` but the integration test doesn't read back `info.config.params.sparse_vectors["bm25"].modifier == Modifier.IDF`. Add an explicit assertion in `TestCreateCollection.test_create_succeeds_with_locked_schema`.

### Lens 5 — Sequencing & dependency correctness

No findings. Step ordering is correct: API inspect → exceptions → client → client tests → stack up → collection → manual smoke → integration tests → script extension → full sweep.

### Lens 6 — Verification command quality

**L6.1 — [minor] Checkpoint A only confirms imports succeed.**
- Strengthen with a positive construction test: `KeywordIndexParams(type="keyword", is_tenant=True)` (or whatever the actual API surface accepts). If construction fails, the spec's API assumption is wrong and Step 6 must adapt.
- Same for `MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM)`.

**L6.2 — [minor] Step 7 manual smoke could be scripted.**
- Replace the `make shell` interactive walkthrough with a `python -c` or one-shot `docker compose exec` script. Reduces operator error and makes the smoke reproducible across re-runs. Acceptable to keep manual; document the ergonomic trade-off.

### Lens 7 — Tooling correctness

**L7.1 — [minor] `make` targets used in plan vs Makefile.**
- Plan uses `make up`, `make down`, `make ps`, `make health`, `make shell`. All present in the Makefile (verified by reading).
- Plan does NOT use `make dev-up` (override mode) — production mode (`make up`) is the canonical Phase 3 target since it coexists with host services per the Makefile help text.

**L7.2 — [minor] Host pytest of `test_qdrant_collection.py` skip semantics.**
- Plan documents (Risk #15, Ambiguity #8) that host runs skip. The fixture's `pytest.skip(...)` produces an `s` in the verbose output, not a fail. Confirm the implementation report calls this out so the user doesn't mistake skips for failures.

### Lens 8 — Risk register completeness

**L8.1 — [minor] `MultiVectorConfig` parameter location.**
- Some versions accept it inside `VectorParams(multivector_config=...)`; others require a separate `multivectors_config` kwarg on `create_collection`. Step 1 must verify.

**L8.2 — [minor] Test runtime growth.**
- Phase 1 (1) + Phase 2 (38) + Phase 3 (~10 client + ~6 collection = ~16) ≈ 55 tests. Integration tests add ~10–30s per test for Qdrant round-trips. Full host suite stays under 1 minute; full container suite ~30–90s. Flag for Phase 8.

**L8.3 — [minor] `lru_cache(maxsize=1)` on no-arg function.**
- Currently `get_qdrant_client()` takes no arguments — cache works as singleton. If a future change adds an argument (e.g., `tenant_id` for multi-tenant client pools), the cache becomes per-arg, not singleton. Defensive note in implementation report.

---

## Findings escalated to user

**No findings require user input before Prompt 3.**

The plan's §6 ambiguity #1 (qdrant-client API drift) is the dominant risk; it's resolved by Step 1's API inspection and adapt-syntax-preserve-semantics rule. Ambiguity #7 (`bot_id="rt"` slug-invalid) is a known spec defect with a mechanical fix (`"rt0"`).

---

## Recommendation

**Ready for Prompt 3.** Revisions to plan.md (rev 2):

1. **Step 1 expansion** — add `delete`, `upsert` signature inspections plus positive-construction tests for `KeywordIndexParams(type="keyword", is_tenant=True)` and `MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM)`. [resolves L2.1, L6.1]
2. **§4 risk register** — add Risks #17–#20 (no-new-deps guardrail, parallelism assumption, API-key rotation, `lru_cache` arg evolution). [resolves L1.1, L2.2, L2.3, L8.3]
3. **§3 preamble** — echo "no business endpoints" hard constraint. [resolves L1.2]
4. **§3 step 8 / Checkpoint H** — strengthen `test_create_succeeds_with_locked_schema` to assert `sparse_vectors["bm25"].modifier == Modifier.IDF`. [resolves L4.1]
5. **§6 ambiguities** — add #11 (`MultiVectorConfig` placement), #12 (Phase 5 timeout review), #13 (`drop_collection` safety gate for Phase 8). [resolves L8.1, L3.2, L3.3]
6. **§9 cheat-sheet** — note that host pytest skips of `test_qdrant_collection.py` are EXPECTED and acceptance #8 explicitly accepts skip-or-pass. [resolves L7.2]
