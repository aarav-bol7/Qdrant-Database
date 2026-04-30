# Phase 3 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to BUILD what the revised plan describes, then VERIFY it against the spec, then REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_3_qdrant_layer/spec.md` — re-read in full.
2. `build_prompts/phase_3_qdrant_layer/plan.md` — the revised plan from Step 2. Your roadmap.
3. `build_prompts/phase_3_qdrant_layer/plan_review.md` — the critique. Don't re-litigate.
4. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract (locked).
5. `build_prompts/phase_2_domain_models/implementation_report.md` — the `Document.bot_ref` rename and other Phase 2 outcomes.
6. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract (locked).
7. `README.md` — project context (skim).

If any of `spec.md`, `plan.md`, or `plan_review.md` for Phase 3 is missing, abort.

---

## Hard rules during implementation

1. **Follow the revised plan.** Deviations must be justified in the final report.
2. **Build in the order the plan specifies.**
3. **Run the plan's verification commands at every checkpoint.** Don't accumulate broken state.
4. **Honor every "Out of scope" item.** Do NOT write embedder, chunker, DRF serializers, API views, gRPC server. Phase 3 ends where the plan ends.
5. **Do NOT modify Phase 1 or Phase 2 files** except the explicit `scripts/verify_setup.py` extension.
6. **No code comments unless the spec or a non-obvious invariant justifies them.**
7. **Never commit `.env`.**
8. **Verify the installed `qdrant-client` API matches what the spec sketches BEFORE writing collection.py.** Use `inspect.signature` or `dir()` on a Python prompt to check method names and parameter names. If the actual API differs, adapt while preserving the SEMANTICS specified.
9. **Test collections must be cleaned up.** Even on test failure. Use `try/finally` or pytest yield-fixture teardown.
10. **No emoji in code or comments. No documentation files (`*.md`) beyond `implementation_report.md` unless spec explicitly requires.**

---

## Implementation phases

### Phase A — qdrant-client API verification (pre-implementation)

Before writing collection.py, verify the installed qdrant-client's API surface matches what the spec assumes. Run from the host:

```bash
uv run python -c "
from qdrant_client import QdrantClient
from qdrant_client import models
from inspect import signature

print('--- create_collection signature ---')
print(signature(QdrantClient.create_collection))
print('--- create_payload_index signature ---')
print(signature(QdrantClient.create_payload_index))
print('--- collection_exists exists? ---')
print(hasattr(QdrantClient, 'collection_exists'))
print('--- KeywordIndexParams fields ---')
print(getattr(models, 'KeywordIndexParams', 'MISSING'))
print('--- MultiVectorConfig fields ---')
print(getattr(models, 'MultiVectorConfig', 'MISSING'))
print('--- MultiVectorComparator values ---')
print(list(getattr(models, 'MultiVectorComparator', [])))
"
```

Document the result. If anything differs from the spec sketch, choose the closest semantic equivalent and document the deviation in the final report.

### Phase B — Exceptions

Create `apps/qdrant_core/exceptions.py` per the spec.

**Verify:** `uv run python -c "from apps.qdrant_core.exceptions import QdrantError, QdrantConnectionError, CollectionSchemaMismatchError, QdrantOperationError; print('ok')"`

### Phase C — Client

Create `apps/qdrant_core/client.py` with the singleton + retry decorator.

**Verify (without hitting Qdrant):**
```bash
uv run python -c "from apps.qdrant_core.client import get_qdrant_client, with_retry, _is_transient; print('imports ok')"
uv run pytest tests/test_qdrant_client.py -v   # if test file exists by this point; otherwise skip
```

### Phase D — Collection

Create `apps/qdrant_core/collection.py`. This is the largest single file in the phase. Use the API surface verified in Phase A. Match the spec's semantics; allow API-level deviations only.

**Verify (without hitting Qdrant):**
```bash
uv run python -c "from apps.qdrant_core.collection import create_collection_for_bot, get_or_create_collection, delete_by_doc_id, drop_collection, _compare_schema; print('imports ok')"
uv run python manage.py check
```

### Phase E — Unit tests for client (no Qdrant required)

Create `tests/test_qdrant_client.py`. These tests don't need a real Qdrant — they exercise the singleton pattern and retry decorator with fake errors.

**Verify:**
```bash
uv run pytest tests/test_qdrant_client.py -v
```
All green. No `@pytest.mark.django_db` marker because no ORM access.

### Phase F — Bring stack up

```bash
make down
make up
sleep 60
make ps                                          # all healthy
make health                                      # green JSON (Phase 1 + 2 regression)
```

If web is unhealthy, abort and check logs.

### Phase G — Integration tests for collection (real Qdrant required)

Create `tests/test_qdrant_collection.py`. These tests run against the real Qdrant container.

**From the host:**
```bash
uv run pytest tests/test_qdrant_collection.py -v
```

If Qdrant isn't reachable from `localhost:6334` (it should be, per `make up` + Compose port mapping), the session-scoped fixture skips the suite gracefully.

**From inside the web container:**
```bash
docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v
```

Both must be green. Inspect Qdrant after the run to confirm no orphan test collections:
```bash
docker compose -f docker-compose.yml exec web python -c "
from apps.qdrant_core.client import get_qdrant_client
print([c.name for c in get_qdrant_client().get_collections().collections if 'test' in c.name])
"
```
Expected: `[]`.

### Phase H — verify_setup.py extension

Extend `scripts/verify_setup.py` per the spec. Phase 1's behavior MUST be preserved exactly.

**Verify:**
```bash
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py            # Phase 1 mode — must still work
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full     # Phase 3 round-trip
```

Both must exit 0. After the `--full` run, no orphan collections (run the same inspection as Phase G).

### Phase I — Full suite + regression

```bash
uv run pytest -v                                  # all tests including Phase 1 & 2
uv run ruff check .
uv run ruff format --check .
make health                                       # one final smoke
git status --short                                # ONLY Phase 3 files changed
```

Files in `git status --short` must be:
- `apps/qdrant_core/exceptions.py` (new)
- `apps/qdrant_core/client.py` (new)
- `apps/qdrant_core/collection.py` (new)
- `scripts/verify_setup.py` (modified)
- `tests/test_qdrant_client.py` (new)
- `tests/test_qdrant_collection.py` (new)
- Optionally: `uv.lock` if a transient bump occurred (must be reviewed and committed if so)
- `build_prompts/phase_3_qdrant_layer/implementation_report.md` (new)

**No other file should be in the diff.** If anything else is, that's a deviation requiring justification.

---

## Self-review

After Phase I passes, run this self-review against the **spec** (not the plan).

For each acceptance criterion in `spec.md` (all 10), record:
- **Pass / fail** (be honest)
- **Verification command run** (paste it)
- **Output observed** (paste relevant lines)

For each pitfall (all 10), record:
- **Avoided / hit / not-applicable**
- **How confirmed**

For each "Out of scope" item, confirm not implemented.

---

## Final report

Save to `build_prompts/phase_3_qdrant_layer/implementation_report.md`. Structure:

```markdown
# Phase 3 — Implementation Report

## Status
**OVERALL:** PASS / FAIL / PARTIAL

## Summary
- Files created: <N>
- Files modified outside Phase 3 scope: <N> (must be ≤ 1; only verify_setup.py)
- Tests added: <N>
- Tests passing: <N>/<N>
- Acceptance criteria passing: <N>/10

## qdrant-client API verification (Phase A)
[Paste the output of the API surface inspection. Note any deviations from the spec sketch and how they were adapted in code.]

## Acceptance criteria
### Criterion 1: <copy from spec>
- Result: PASS / FAIL
- Command: `<exact>`
- Output:
  ```
  <relevant lines>
  ```
- Notes: <caveats>
[... repeat for all 10]

## Pitfall avoidance
### Pitfall 1: <copy from spec>
- Status: Avoided / Hit / N/A
- How confirmed: <command or reasoning>

## Out-of-scope confirmation
[brief list: each "out of scope" item with "confirmed not implemented"]

## Phase 1 + Phase 2 regression check
- Phase 1 acceptance criteria still pass:
  - /healthz returns green JSON on port 8080: <output>
  - Container healthchecks all green: <output>
- Phase 2 acceptance criteria still pass:
  - All Phase 2 tests still green: <output>
  - Admin login still loads: <output>
- No Phase 1 or Phase 2 file modified except `scripts/verify_setup.py` (extension): paste `git diff --name-only` output to prove it.

## Deviations from plan
[for each deviation: what · why · impact]

## qdrant-client API deviations from spec sketch
[any places where the actual qdrant-client API required different syntax than the spec's sketch]

## Spec defects discovered
[anything in spec.md that turned out to be incorrect, contradictory, or impossible]

## Outstanding issues
[non-blocking but worth knowing before Phase 4]

## Files created or modified
[clean tree]

## Commands to verify the build (one block, copy-pasteable)

```bash
make down
make up
sleep 60
make ps
make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

## Verdict
One paragraph: is Phase 3 ready to ship? Next step?
```

---

## What "done" looks like for this prompt

Output to chat:

1. Path to `implementation_report.md`.
2. **Overall status: PASS / FAIL / PARTIAL.**
3. Acceptance criteria score: `X/10 passed`.
4. qdrant-client API deviations summary (one line).
5. Phase 1 + Phase 2 regression check status (PASS / FAIL).
6. Recommended next step.

Then **stop**.

---

## A note on honesty

If something is broken — including the qdrant-client API not matching the spec — say so. A failing build reported as failing is more useful than a passing build that quietly worked around a real defect. The report is the contract — write it to be true, not flattering.
