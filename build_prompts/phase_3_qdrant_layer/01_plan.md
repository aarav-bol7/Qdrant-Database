# Phase 3 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to PLAN, not to write code. Do not create source files. Do not modify any Phase 1 or Phase 2 file. Do not start a Qdrant collection.**

---

## Required reading (in this order)

1. `README.md` — project charter; understand where Phase 3 fits.
2. `build_prompts/phase_3_qdrant_layer/spec.md` — the full Phase 3 specification. **Source of truth. Read it twice.**
3. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract; `apps/qdrant_core/naming.py` (collection_name + advisory_lock_key) is what Phase 3 builds on.
4. `build_prompts/phase_2_domain_models/implementation_report.md` — confirms Phase 2 deliverables, including the `Document.bot_ref` rename.
5. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract; understand the locked compose / settings / structlog setup.
6. `rag_system_guide.md` if present — §6 has the per-bot vector-schema rationale.

If `phase_3_qdrant_layer/spec.md` does not exist, abort with a clear error.

---

## Your task

Produce a structured implementation plan. Save it to:

```
build_prompts/phase_3_qdrant_layer/plan.md
```

Step 2 (`02_review.md`) will critique it; Step 3 (`03_implement.md`) will execute it.

---

## What the plan must contain

Use this exact section structure, in this order:

### 1. Plan summary

3–5 sentence executive summary at the top (write last). What's being built? What's the riskiest part? How will the build verify itself?

### 2. Build order & dependency graph

Enumerate every file in spec.md's "Deliverables" tree (5 new + 1 extended = 6 entries). For each: path · what Phase 3 needs it to do · what it depends on (Phase 1/2 components or other Phase 3 files) · which build step creates it.

### 3. Build steps (sequenced)

A numbered list of 8–14 build steps. Each step:
- **Goal** (one sentence)
- **Files touched** (paths)
- **Verification command**
- **Rollback action**

The first steps must be the dependency-free foundation: `apps/qdrant_core/exceptions.py` (no imports from elsewhere in the project). Then `apps/qdrant_core/client.py` (depends on exceptions + Django settings + qdrant-client + grpc). Then `apps/qdrant_core/collection.py` (depends on naming.py + client + exceptions). Then unit tests for the client (no Qdrant required), then integration tests for collection (real Qdrant required), then the script extension, then full-stack smoke.

### 4. Risk register

For each plausible failure mode: risk · likelihood · impact · mitigation · detection. Cover at minimum:

- **qdrant-client API drift**: the spec sketches a particular API surface (`KeywordIndexParams`, `MultiVectorConfig`, `client.collection_exists`, etc.); the installed version may have different names. Plan must include a "verify imports + signatures against installed qdrant-client first" step.
- **ColBERT vector size set to 128 instead of 1024** — would silently break Phase 4. Mitigation: explicit constant + assertion in collection.py.
- **`HnswConfigDiff(m=0)` interpreted as default `m` instead of disabled** — verify by reading back `info.config.params.vectors["colbert"].hnsw_config.m`.
- **`is_tenant=True` API location wrong** — depends on qdrant-client version. Plan must verify against the actual installed API.
- **Singleton constructed pre-fork** — `lru_cache` on `get_qdrant_client()` is correct iff it's called only inside functions, never at module import time. Plan must verify no module-level call exists.
- **Retry decorator catches `BaseException`** — must use `except Exception` to avoid catching `KeyboardInterrupt`/`SystemExit`.
- **Concurrent get_or_create races** — two workers see "missing" simultaneously, both call create, one gets 409. Plan must include a 409-handling path that verifies and continues, not raises.
- **Test collections orphaned after failed tests** — fixture must drop in `try/finally` (or yield + teardown).
- **Tests use uppercase or hyphens in test collection names** — would fail slug regex. Plan must use lowercase + underscore.
- **`scripts/verify_setup.py --full` runs but Qdrant unreachable** — must fail loudly, not silently.
- **Phase 1/2 regression** — accidentally edited a file outside Phase 3 scope. Plan's verification must include `git status --short` to confirm only Phase 3 files changed.

### 5. Verification checkpoints

Pause-and-verify points with exact commands and expected outcomes. Aim for 8–12 checkpoints:

- After exceptions.py: `uv run python -c "from apps.qdrant_core.exceptions import QdrantConnectionError, CollectionSchemaMismatchError, QdrantOperationError; print('ok')"`
- After client.py: `uv run python -c "from apps.qdrant_core.client import get_qdrant_client, with_retry; print('ok')"` (don't actually call get_qdrant_client at this point — that requires Qdrant up)
- After collection.py: `uv run python -c "from apps.qdrant_core.collection import create_collection_for_bot, get_or_create_collection, delete_by_doc_id, drop_collection; print('ok')"`
- After test_qdrant_client.py: `uv run pytest tests/test_qdrant_client.py -v` — all green, doesn't require Qdrant.
- After bringing the stack up: `make up && sleep 60 && make health` (or equivalent docker compose commands).
- After test_qdrant_collection.py from inside the web container: `docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v`.
- After verify_setup.py extension: `docker compose -f docker-compose.yml exec web python scripts/verify_setup.py` (Phase 1 mode — must still work) AND `docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full` (Phase 3 round-trip).
- After full suite: `uv run pytest -v` — Phase 1 + Phase 2 + Phase 3 tests all green.
- Phase 1/2 regression: `curl -fsS http://localhost:8080/healthz | python -m json.tool`.
- Out-of-scope guard: `git status --short` shows ONLY changes to the 6 expected files.

### 6. Spec ambiguities & open questions

Read the spec critically. Wherever it underspecifies something, list:
- **What's ambiguous**
- **Your proposed interpretation** (don't ask the user — make a call)
- **Reversibility**

Things to scrutinize:
- The spec sketches `qdrant_client.models.KeywordIndexParams(type="keyword", is_tenant=True)`. Is the actual API like this in the installed version? If not, what's the equivalent?
- `client.collection_exists(name)` — does the installed qdrant-client have this method, or do we need `client.get_collections()` + name check?
- `client.count(name, count_filter=Filter(...), exact=True)` — does the parameter name match?
- The spec's `_compare_schema()` checks dense.size, dense.distance, colbert.size, colbert.multivector_config presence, sparse.bm25 presence + modifier. Should we also check HNSW m / ef_construct values, or is that overkill?
- Test isolation: each test creates a unique `(tenant_id, bot_id)` via uuid. If a test crashes mid-execution, the fixture's teardown still drops the collection. But what about the @with_retry decorator's retry on connection errors — could it retry a drop_collection call against a collection that's in the middle of being created? Edge case, probably benign (drop is idempotent).
- The session-scoped `qdrant_available` fixture in test_qdrant_collection.py — does pytest-django's DB setup interfere with it? Should it be function-scoped? Trade-off: session-scope skips faster if Qdrant is down for the whole test run; function-scope re-checks each test.
- The `verify_setup.py --full` round-trip uses `f"verify_{int(time.time())}"` as tenant_id. This is slug-compliant. Should we also add a `verifyrt` (4 chars) bot_id to make it cleaner, or stick with `rt` (must be 3+ chars per slug regex)?
- Is `verifyrt` 8 chars or `rt` 2 chars? — Slug regex requires 3+ chars. So `rt` is INVALID. Must be at least 3.

Aim for 5–10 entries.

### 7. Files deliberately NOT created / NOT modified

- Echo the spec's "Out of scope" list in your own words.
- Add the explicit Phase 1 + Phase 2 don't-touch list (every file under `apps/tenants`, `apps/documents`, `apps/core`, `config/`, `tests/test_models.py`, `tests/test_naming.py`, `tests/test_healthz.py`, `tests/test_settings.py`, `tests/conftest.py`, `Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`, `pyproject.toml`, `.env.example`, `manage.py`, `Makefile`, etc.).
- Note that `apps/ingestion/`, `apps/grpc_service/` stay untouched.

### 8. Acceptance-criteria mapping

For each of the 10 acceptance criteria in spec.md: criterion summary · which build step satisfies it · verification command · expected output. Every criterion must map to at least one step.

### 9. Tooling commands cheat-sheet

The exact commands you'll use. At minimum:

```
# Inspect installed qdrant-client API surface (do this BEFORE writing collection.py)
uv run python -c "from qdrant_client.models import KeywordIndexParams; print(KeywordIndexParams.__doc__)"
uv run python -c "from qdrant_client import QdrantClient; print([m for m in dir(QdrantClient) if not m.startswith('_')])"

# Standard build commands
uv run pytest tests/test_qdrant_client.py -v
uv run pytest tests/test_qdrant_collection.py -v
uv run pytest -v
uv run ruff check .
uv run ruff format --check .

# Docker
make up                                                                 # build + start (port 8080)
make health                                                             # smoke
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v
make down
```

### 10. Estimated effort

A rough wall-clock estimate per build step.

---

## Output format

A single markdown file at `build_prompts/phase_3_qdrant_layer/plan.md`. Use clear headings, tables, code blocks. Aim for 400–700 lines.

---

## What "done" looks like for this prompt

When finished, output to chat:

1. Confirmation that `plan.md` was created.
2. Total line count.
3. A 5-bullet summary of the plan's key sequencing decisions.
4. Spec ambiguities flagged in section 6 (just titles).

Then **stop**. Do NOT begin implementation.
