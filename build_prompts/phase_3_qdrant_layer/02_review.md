# Phase 3 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to CRITIQUE the plan from Step 1 and revise it. Do not write production code. The only files you create are the revised plan and a critique document.**

---

## Required reading (in this order)

1. `build_prompts/phase_3_qdrant_layer/spec.md` — source of truth.
2. `build_prompts/phase_3_qdrant_layer/plan.md` — the plan from Step 1.
3. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract (locked).
4. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract (locked).
5. `README.md` — context (skim).

If `plan.md` does not exist, abort: `"Plan not found. Run PROMPT 1 first."`

---

## Your task

Adversarially review the plan. Find every gap, wrong assumption, missed edge case, and production-readiness flaw. Then produce a revised plan that addresses every finding.

Save outputs to:

- `build_prompts/phase_3_qdrant_layer/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_3_qdrant_layer/plan.md` — overwritten with the revised plan

Keep the original plan's section structure (1–10) — your revision adds, corrects, extends.

---

## Review lenses

For each lens, list findings (or `"no findings"`). Tag each: **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

For every requirement in `spec.md`, verify the plan addresses it:

- All 6 deliverable files
- All 12 hard constraints
- All 10 acceptance criteria
- All 10 common pitfalls
- The "Out of scope" list

Flag any requirement the plan misses or handles incorrectly.

### Lens 2 — Edge cases the plan missed

For each step in the plan, ask "what could go wrong here?" At minimum:

- **qdrant-client version drift.** The spec sketches a particular API; the installed version might differ. Does the plan begin with an "inspect installed API surface" step before writing collection.py? If not, the agent might write code against a hypothetical API that doesn't exist.
- **`MultiVectorConfig` import path.** Across qdrant-client versions, this has lived in different submodules. Does the plan check `qdrant_client.models.MultiVectorConfig` is the right import?
- **Sparse vector creation.** Does the installed `qdrant-client` accept `sparse_vectors_config={...}` as a top-level argument to `create_collection()`, or does it require a different shape? Plan should verify with a 1-line `inspect.signature`.
- **`is_tenant=True` actual API.** May not be `KeywordIndexParams(type="keyword", is_tenant=True)`. May be a separate parameter on `create_payload_index`. Plan must verify.
- **Schema comparison method (`_compare_schema`)** — if `info.config.params.vectors` is a dict-like AND the spec accesses `.get("dense")`, what if it's a `NamedVectorsConfig` object with attribute access? Plan should handle either.
- **Test fixture scope.** `@pytest.fixture(scope="session")` for `qdrant_available` is efficient but couples all tests in the session. If one test corrupts the connection (unlikely with proper teardown), all subsequent tests fail. Plan should consider function-scope as a robustness vs. speed trade-off.
- **`drop_collection` in fixture teardown.** What if `drop_collection` raises (e.g., transient connection error)? The `try/except: pass` swallows it but leaves an orphan. Plan should at least log the failure.
- **Test parallelism.** If tests run in parallel (e.g., `pytest -n 4`), unique-uuid collection names prevent collision. But `qdrant_available` session-scoped fixture might not interact well with parallel workers. Plan should document the assumption (sequential test runs).
- **Settings cache.** `settings.QDRANT["API_KEY"]` is read once when `get_qdrant_client` first runs. If the API key rotates without a worker restart, the cached client uses the stale key. v1 deferral, but worth noting.
- **`functools.lru_cache` doesn't survive `os.fork()` cleanly.** Each forked worker re-evaluates the cache on first call. This is the desired behavior (each worker gets its own client). Plan should confirm via the test fixture pattern.
- **`grpc.RpcError` subclass detection.** `_is_transient` uses `isinstance(exc, grpc.RpcError)`. But qdrant-client may wrap gRPC errors in its own exception class. Plan should test what kinds of exceptions the qdrant-client surface actually raises.
- **`UnexpectedResponse` import.** In some qdrant-client versions this is at `qdrant_client.http.exceptions`, in others it's elsewhere. Verify.
- **Phase 1's healthz uses `_get_qdrant_client()` from `apps/core/views.py`** — a SEPARATE client instance from Phase 3's `get_qdrant_client()`. Two singletons in two modules. Is this intentional? Plan should acknowledge — both are fine because they're per-module caches and the underlying QdrantClient is stateless enough. But if they diverge in config (e.g., one passes `timeout=2`, other `timeout=10`), it's confusing. Document the decision.

### Lens 3 — Production-readiness gaps

- **Connection pool / channel reuse.** qdrant-client's gRPC channel is reused across calls within a single client instance. Verify the plan doesn't accidentally instantiate multiple clients per worker.
- **Logging cardinality.** Every retry attempt logs at WARNING. In a transient outage with high request volume, this could flood logs. Plan should consider rate-limiting or downgrading repeated identical retries to DEBUG.
- **Timeout values.** Spec says client timeout is 10s. For Phase 5's upserts of large batches, 10s might be too short. Plan should flag this for Phase 5's review, not block Phase 3.
- **Schema-mismatch error contains the diff dict.** Is the diff serializable for logs? If a value is a Qdrant model object, it might not be JSON-serializable. Plan should ensure all diff values are strings.
- **`drop_collection` is not behind any safety gate.** Trivial to call by mistake (e.g., a typo in tenant_id) and lose data. Plan should flag this as an operational risk; v1 acceptable but Phase 8's runbook should require explicit confirmation.
- **gRPC keepalive.** Long-lived clients in Celery workers may hit idle-timeout. qdrant-client may not configure keepalive. Plan should flag for future hardening (Phase 8) but not block Phase 3.
- **Health endpoint regression.** Phase 1's `/healthz` instantiates its own `QdrantClient` (separate from Phase 3's). Plan should verify Phase 1's healthz is unchanged.

### Lens 4 — Pitfall coverage audit

For each of spec.md's 10 pitfalls:

1. Does the plan address it explicitly?
2. Does the plan's verification catch it?

If pitfall #1 (ColBERT 128 vs 1024) is in the risk register but verification doesn't read back the actual vector size from a created collection, that's a finding.

### Lens 5 — Sequencing & dependency correctness

Walk the plan's build steps. For each:
- Does it need anything from a later step (circularity)?
- Could it be done earlier?
- If interrupted after this step, is the working dir coherent?

Specifically check:
- exceptions.py first (no deps)
- client.py second (depends on exceptions + Django settings — Django must be configured, which Phase 1 handles)
- collection.py third (depends on client + exceptions + naming.py from Phase 2)
- Tests after the modules they test
- Integration tests require a running Qdrant — plan must `make up` before running them
- script extension after collection.py (uses its helpers)

### Lens 6 — Verification command quality

For each verification command:
- Does it actually verify the goal of the step?
- Does it produce useful output on failure?

Strong verifications:
- After collection creation: `info = client.get_collection(name); assert info.config.params.vectors["colbert"].size == 1024`. Reading back the actual size catches dim mismatches.
- After payload index creation: `assert "doc_id" in info.payload_schema`. Catches index-creation failures.

Weak verifications to flag:
- `assert client.create_collection(...) is not None` — doesn't verify schema, only that the call returned.
- `assert delete_by_doc_id(...) >= 0` — meaningless; integers are always >= 0 unless something weird.

### Lens 7 — Tooling correctness

- `qdrant_client.models` import paths — verify against installed version.
- `make` targets exist for the operations the plan uses (`make up`, `make health`, etc.).
- `pytest` markers — Phase 3's tests don't need `@pytest.mark.django_db` because they don't hit the ORM. Plan should explicitly confirm this.

### Lens 8 — Risk register completeness

Risks the plan may have missed:

- **qdrant-client release notes between minor versions** sometimes change parameter names. Verify with `client.create_collection.__doc__` or `inspect.signature`.
- **`grpc` package wheel compatibility** with Python 3.13 on the runtime image — should be fine since Phase 1 verified, but a dep update could regress.
- **`functools.lru_cache(maxsize=1)` on `get_qdrant_client`** — works for the singleton pattern, but if the function ever takes args (unlikely but worth noting), the cache key behavior changes.
- **Test runner discovers `tests/test_qdrant_collection.py` from the host shell where Qdrant is on `localhost:6334`.** With `prefer_grpc=True` and `host=qdrant`, the test from the HOST resolves "qdrant" to nothing (only resolves inside Docker network). Plan must address: either tests run inside the web container only, or the test fixture overrides `QDRANT_HOST=localhost` for host runs.
- **`MultiVectorConfig` may require a separate `multivectors_config` parameter on `create_collection` instead of being part of `VectorParams`.** Verify.
- **Scaling concern: 39 existing tests + ~15 new Phase 3 tests = ~54 total. Test suite runtime grows.** Probably fine for v1; flag for Phase 8.

---

## Output structure

### File 1: `plan_review.md` (NEW)

```markdown
# Phase 3 Plan Review

## Summary
- Total findings: <N>
- Severity breakdown: <X critical, Y major, Z minor>
- Plan accuracy: <%> spec compliance
- Recommendation: accept revised plan / re-plan / escalate to user

## Findings by lens

### Lens 1 — Spec compliance
1. **[severity] Title.** <description>. Where in plan: <section/line>. Fix: <action>.

### Lens 2 — Missed edge cases
...

### Lens 3 — Production-readiness gaps
...

### Lens 4 — Pitfall coverage audit
...

### Lens 5 — Sequencing & dependency correctness
...

### Lens 6 — Verification command quality
...

### Lens 7 — Tooling correctness
...

### Lens 8 — Risk register completeness
...

## Findings escalated to user
<entries that need user decision before Prompt 3 can run>
```

### File 2: `plan.md` (OVERWRITE)

Same 10-section structure. Add a section 0 at top: **"Revision notes"** — list what changed, with cross-references to `plan_review.md` finding numbers. Resolve all [critical] and [major] findings inline.

---

## What "done" looks like for this prompt

Output to chat:

1. Confirmation both files saved.
2. Severity breakdown.
3. Findings escalated to user (titles only).
4. Recommendation: ready for Prompt 3, or user must weigh in?

Then **stop**.
