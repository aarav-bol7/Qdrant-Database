# Phase 3 — Qdrant Layer · Implementation Plan

> **Source-of-truth spec:** `build_prompts/phase_3_qdrant_layer/spec.md`. Read it first. This document is a sequenced execution guide; it does not change any locked decision. Phase 1 + Phase 2 are green; only `scripts/verify_setup.py` is touched outside `apps/qdrant_core/` and `tests/`.

---

## 0. Revision notes (rev 2)

This is revision 2. See `plan_review.md` for the full critique. Changes vs rev 1:

1. **Step 1 expansion** — added `delete`/`upsert` signature inspections plus positive-construction tests for `KeywordIndexParams(type="keyword", is_tenant=True)` and `MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM)`. [resolves L2.1, L6.1]
2. **§3 preamble** — echoed the "no business endpoints" hard constraint. [resolves L1.2]
3. **§3 Step 8 / Checkpoint H** — strengthened `test_create_succeeds_with_locked_schema` to also assert `sparse_vectors["bm25"].modifier == Modifier.IDF`. [resolves L4.1]
4. **§4 risk register** — added Risks #17–#20 (no-new-deps guardrail, parallelism assumption, API-key rotation, `lru_cache` argument evolution). [resolves L1.1, L2.2, L2.3, L8.3]
5. **§6 ambiguities** — added #11 (`MultiVectorConfig` placement), #12 (Phase 5 timeout review), #13 (`drop_collection` safety gate for Phase 8). [resolves L8.1, L3.2, L3.3]
6. **§9 cheat-sheet** — noted that host pytest skips of `test_qdrant_collection.py` are EXPECTED. [resolves L7.2]

No critical findings emerged from the review. One major finding (Step 1 inspection coverage) was a mechanical fix; fifteen minor findings were either folded into the risk register or surfaced as ambiguities. No architectural decisions deferred to user input.

---

## 1. Plan summary

Phase 3 ships the Qdrant integration layer the upload (Phase 5), delete (Phase 6), and search (Phase 7) phases will consume: typed exceptions, a singleton gRPC client with retry/backoff, and a per-bot collection factory enforcing the locked vector schema (dense 1024 + sparse `bm25` IDF + ColBERT 1024-per-token max-sim with HNSW disabled) plus 8 payload indexes. The single riskiest step is `apps/qdrant_core/collection.py` — the spec sketches a particular `qdrant-client` API (`KeywordIndexParams(is_tenant=True)`, `MultiVectorConfig`, `client.collection_exists`, `client.count(count_filter=...)`) that may have drifted in the installed wheel, so the build front-loads an "inspect installed API surface" step before any collection code is written. Verification is layered: import smokes per module, unit tests for the client (no Qdrant), real-Qdrant integration tests that read back actual vector sizes / multivector flags / payload-schema entries, a `verify_setup.py --full` round-trip, and a Phase-1/2 regression sweep (`/healthz` + `pytest`).

---

## 2. Build order & dependency graph

| # | File | Purpose | Depends on | Created by step |
|---|---|---|---|---|
| 1 | `apps/qdrant_core/exceptions.py` | `QdrantError` base + `QdrantConnectionError`, `CollectionSchemaMismatchError`, `QdrantOperationError` | stdlib only | Step 2 |
| 2 | `apps/qdrant_core/client.py` | Lazy `lru_cache`-wrapped `get_qdrant_client()`, `_is_transient`, `with_retry` decorator | exceptions + `django.conf.settings` + `qdrant_client.QdrantClient` + `grpc` | Step 3 |
| 3 | `tests/test_qdrant_client.py` | Unit tests for singleton, `_is_transient`, retry decorator (no Qdrant required) | client + exceptions | Step 4 |
| 4 | `apps/qdrant_core/collection.py` | `create_collection_for_bot`, `get_or_create_collection`, `delete_by_doc_id`, `drop_collection`, `_compare_schema`; locked schema constants | client + exceptions + Phase 2's `apps.qdrant_core.naming.collection_name` + `qdrant_client.models.*` | Step 6 |
| 5 | `tests/test_qdrant_collection.py` | Real-Qdrant integration tests; skips gracefully if Qdrant unreachable | collection + client + naming | Step 8 |
| 6 | `scripts/verify_setup.py` (EXTEND) | Adds `--full` flag → create→upsert→delete→drop round-trip; preserves Phase 1's `ping_postgres()` + `ping_qdrant()` | collection + client; argparse | Step 9 |

**Dependency graph (top → bottom):**

```
django.conf.settings (Phase 1)
        │
        ▼
apps.qdrant_core.exceptions       ◄── stdlib only
        │
        ▼
apps.qdrant_core.client           ◄── exceptions + settings + qdrant_client + grpc
        │
        ▼
apps.qdrant_core.collection       ◄── client + exceptions + naming (Phase 2) + qdrant_client.models
        │
        ▼
tests/test_qdrant_client.py , tests/test_qdrant_collection.py , scripts/verify_setup.py (extend)
```

The lazy `lru_cache` on `get_qdrant_client()` is the cycle-breaker for fork-safety: nothing at module import time constructs a gRPC channel, so gunicorn's master never holds one before the workers fork.

---

## 3. Build steps (sequenced)

> **Spec hard constraint #11:** no comments unless the spec or a non-obvious invariant justifies them. Module-level docstrings from the spec body are kept; defensive prose-comments in function bodies are not added.
> **Spec hard constraint #12:** still only `/healthz` and `/admin/`. No business endpoints, no `/v1/...`, no embedder, no chunker.
> **Spec hard constraint #2:** no new dependencies. `qdrant-client` is already pinned in `uv.lock` from Phase 1. If `uv add` is reached for, the implementer is solving the wrong problem.

### Step 1 — Inspect the installed `qdrant-client` API surface (NO code yet)
- **Goal:** confirm class names, import paths, parameter names, and constructor kwargs match what the spec sketches; capture any drift before writing code.
- **Files touched:** none (inspection only).
- **Commands:**
  ```bash
  # Imports
  uv run python -c "from qdrant_client import QdrantClient; print('methods:', sorted(m for m in dir(QdrantClient) if not m.startswith('_')))"
  uv run python -c "from qdrant_client.models import KeywordIndexParams, MultiVectorConfig, MultiVectorComparator, Modifier, SparseIndexParams, SparseVectorParams, VectorParams, HnswConfigDiff, Distance, PayloadSchemaType, Filter, FieldCondition, MatchValue, PointStruct, SparseVector; print('all imports ok')"
  uv run python -c "from qdrant_client.http.exceptions import UnexpectedResponse; print('ok')"

  # Method signatures
  uv run python -c "import inspect; from qdrant_client import QdrantClient; print('create_collection:', inspect.signature(QdrantClient.create_collection))"
  uv run python -c "import inspect; from qdrant_client import QdrantClient; print('create_payload_index:', inspect.signature(QdrantClient.create_payload_index))"
  uv run python -c "import inspect; from qdrant_client import QdrantClient; print('count:', inspect.signature(QdrantClient.count))"
  uv run python -c "import inspect; from qdrant_client import QdrantClient; print('delete:', inspect.signature(QdrantClient.delete))"
  uv run python -c "import inspect; from qdrant_client import QdrantClient; print('upsert:', inspect.signature(QdrantClient.upsert))"
  uv run python -c "import inspect; from qdrant_client import QdrantClient; print('collection_exists:', hasattr(QdrantClient, 'collection_exists'))"

  # Positive-construction tests (catches breaking changes in model classes)
  uv run python -c "from qdrant_client.models import KeywordIndexParams; k = KeywordIndexParams(type='keyword', is_tenant=True); print('KeywordIndexParams ok:', k)"
  uv run python -c "from qdrant_client.models import MultiVectorConfig, MultiVectorComparator; m = MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM); print('MultiVectorConfig ok:', m)"
  uv run python -c "from qdrant_client.models import VectorParams, Distance, MultiVectorConfig, MultiVectorComparator, HnswConfigDiff; v = VectorParams(size=1024, distance=Distance.COSINE, multivector_config=MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM), hnsw_config=HnswConfigDiff(m=0)); print('multivector inside VectorParams ok')"
  uv run python -c "from qdrant_client.models import SparseVectorParams, SparseIndexParams, Modifier; s = SparseVectorParams(index=SparseIndexParams(on_disk=False), modifier=Modifier.IDF); print('SparseVectorParams ok:', s)"
  ```
- **Verification (Checkpoint A):** every import succeeds; every constructor returns a non-None object; `create_collection` signature contains a `sparse_vectors_config` parameter; `count` uses `count_filter` (or document the actual name); `create_payload_index` accepts `field_schema`; `delete` accepts `points_selector`; `upsert` accepts `points`. **If any of these differ from spec, document the drift in the implementation report and adapt collection.py accordingly — do NOT change spec semantics, only adjust syntax.**
- **Rollback:** none (no files written).

### Step 2 — Write `apps/qdrant_core/exceptions.py`
- **Goal:** typed exception classes the rest of the layer raises.
- **Files touched:** `apps/qdrant_core/exceptions.py` (NEW).
- **Body:** verbatim from spec §"`apps/qdrant_core/exceptions.py`" — `QdrantError` base, `QdrantConnectionError`, `CollectionSchemaMismatchError(collection_name, diff)`, `QdrantOperationError`.
- **Verification (Checkpoint B):**
  ```bash
  uv run python -c "from apps.qdrant_core.exceptions import QdrantError, QdrantConnectionError, CollectionSchemaMismatchError, QdrantOperationError; e = CollectionSchemaMismatchError('c', {'x': 'y'}); assert e.collection_name == 'c' and e.diff == {'x':'y'}; print('ok')"
  ```
- **Rollback:** `rm apps/qdrant_core/exceptions.py`.

### Step 3 — Write `apps/qdrant_core/client.py`
- **Goal:** singleton `QdrantClient` with retry/backoff on transient gRPC + qdrant-client wrapped errors. Module import does NOT touch the network.
- **Files touched:** `apps/qdrant_core/client.py` (NEW).
- **Body:** verbatim from spec §"`apps/qdrant_core/client.py`": `get_qdrant_client()` decorated `@functools.lru_cache(maxsize=1)`, `_RETRYABLE_GRPC_CODES` set (`UNAVAILABLE`, `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`), `_is_transient`, `with_retry(*, attempts=3, initial_delay=0.5, backoff=2.0)`. The decorator's `except Exception` (NOT `BaseException`) is critical — must NOT swallow `KeyboardInterrupt`/`SystemExit`.
- **Verification (Checkpoint C):**
  ```bash
  uv run python -c "from apps.qdrant_core.client import get_qdrant_client, with_retry, _is_transient; print('ok')"
  ```
  Do **NOT** call `get_qdrant_client()` here — that requires Qdrant up, which is Step 7's responsibility.
- **Rollback:** `rm apps/qdrant_core/client.py`.

### Step 4 — Write `tests/test_qdrant_client.py` and run
- **Goal:** lock the singleton + retry behavior with unit tests that don't need a real Qdrant.
- **Files touched:** `tests/test_qdrant_client.py` (NEW).
- **Body:** verbatim from spec §"`tests/test_qdrant_client.py`" — `TestSingleton` (returns same instance, cache_clear reinitialises), `TestIsTransient` (UNAVAILABLE transient, INVALID_ARGUMENT not, ValueError not), `TestRetryDecorator` (success first try, retries-then-succeeds, exhausted raises QdrantConnectionError, non-transient propagates immediately). All test classes omit `@pytest.mark.django_db` per spec hard constraint #10 — these don't touch the ORM.
- **Verification (Checkpoint D):**
  ```bash
  uv run pytest tests/test_qdrant_client.py -v
  ```
  Expect: every test green, no DB-access errors, no network calls.
- **Rollback:** `rm tests/test_qdrant_client.py`.

### Step 5 — Bring the stack up (production mode)
- **Goal:** Qdrant container reachable from web container so Step 6's collection module can be exercised end-to-end (and later integration tests + verify_setup --full).
- **Commands:**
  ```bash
  make up              # docker compose -f docker-compose.yml up -d --build
  sleep 60             # let healthchecks settle
  make ps              # all containers healthy/running
  make health          # /healthz returns 200 with both components ok
  ```
- **Verification (Checkpoint E):** `make health` prints `{"status":"ok",...}` with `postgres: ok`, `qdrant: ok`. **If host-side blockers fire** (docker socket permission denied or port conflicts on 5432/6379/8080), follow the Phase 1 implementation_report fixes — those are unchanged from Phase 1/2 outstanding issues. Don't re-debug.
- **Rollback:** `make down` (no data loss; volumes preserved).

### Step 6 — Write `apps/qdrant_core/collection.py`
- **Goal:** the heart of Phase 3. Locked schema constants + four helpers. Adapt syntax to whatever Step 1 surfaced; preserve semantics.
- **Files touched:** `apps/qdrant_core/collection.py` (NEW).
- **Body:** spec §"`apps/qdrant_core/collection.py`" verbatim, with three pre-known adaptation points:
  1. **`KeywordIndexParams(type="keyword", is_tenant=True)`** — if Step 1 reveals the constructor doesn't accept `type=` (some versions infer from class), drop it; if `is_tenant` is missing, surface as a [critical] spec defect and stop.
  2. **`client.collection_exists(name)`** — if missing, replace with `name in [c.name for c in client.get_collections().collections]`. Same semantics, different syntax.
  3. **`client.count(name, count_filter=Filter(...), exact=True).count`** — if the kwarg is `filter=` instead of `count_filter=`, switch. The `.count` attribute access stays.
- **Locked schema constants** (no flexibility): `DENSE_VECTOR_SIZE = 1024`, `DENSE_HNSW_M = 16`, `DENSE_HNSW_EF_CONSTRUCT = 128`, `COLBERT_VECTOR_SIZE = 1024`, `SPARSE_VECTOR_NAME = "bm25"`, `COLBERT_VECTOR_NAME = "colbert"`, `DENSE_VECTOR_NAME = "dense"`. Add an `assert COLBERT_VECTOR_SIZE == 1024` at module bottom as a paranoia guard.
- **Verification (Checkpoint F):**
  ```bash
  uv run python -c "from apps.qdrant_core.collection import create_collection_for_bot, get_or_create_collection, delete_by_doc_id, drop_collection, _compare_schema, COLBERT_VECTOR_SIZE, DENSE_VECTOR_SIZE; assert COLBERT_VECTOR_SIZE == 1024 and DENSE_VECTOR_SIZE == 1024; print('ok')"
  uv run python manage.py check                     # apps still load cleanly
  ```
- **Rollback:** `rm apps/qdrant_core/collection.py`.

### Step 7 — Smoke test from Django shell (manual)
- **Goal:** confirm a real collection round-trip works against the running Qdrant before automating it in tests.
- **Commands:**
  ```bash
  make shell    # spawns python manage.py shell inside the web container
  ```
  Then in the shell:
  ```python
  from apps.qdrant_core.collection import create_collection_for_bot, drop_collection
  from apps.qdrant_core.client import get_qdrant_client
  name = create_collection_for_bot("smoket", "smokeb")  # both slug-valid (3-40 chars)
  print(name)                                            # → t_smoket__b_smokeb
  client = get_qdrant_client()
  info = client.get_collection(name)
  print("dense.size:", info.config.params.vectors["dense"].size)        # → 1024
  print("colbert.size:", info.config.params.vectors["colbert"].size)    # → 1024
  print("colbert.hnsw.m:", info.config.params.vectors["colbert"].hnsw_config.m)  # → 0
  print("sparse:", list((info.config.params.sparse_vectors or {}).keys()))  # → ['bm25']
  print("payload:", sorted(info.payload_schema.keys()))  # 8 fields
  drop_collection("smoket", "smokeb")
  ```
- **Verification (Checkpoint G):** ColBERT size is **1024 not 128**, ColBERT hnsw_config.m is **0**, sparse `bm25` present, all 8 payload indexes appear. Capture the exact field names if the qdrant-client version surfaces them differently.
- **Rollback:** `drop_collection("smoket", "smokeb")` already dropped it; nothing else.

### Step 8 — Write `tests/test_qdrant_collection.py` and run inside the web container
- **Goal:** lock the integration behavior — schema correctness, idempotency, schema-mismatch detection, delete-by-doc filter, drop semantics.
- **Files touched:** `tests/test_qdrant_collection.py` (NEW).
- **Body:** verbatim from spec §"`tests/test_qdrant_collection.py`" with one [minor] strengthening per plan_review L4.1: extend `TestCreateCollection.test_create_succeeds_with_locked_schema` to also assert:
  ```python
  from qdrant_client.models import Modifier
  assert info.config.params.sparse_vectors["bm25"].modifier == Modifier.IDF
  assert info.config.params.vectors["colbert"].hnsw_config.m == 0
  ```
  This catches sparse-without-IDF (pitfall #4) and colbert-HNSW-not-disabled (pitfall #2 / Risk #3) directly in test output.
- **Test fixtures:** session-scoped `qdrant_available` (skips suite if Qdrant unreachable), function-scoped `fresh_bot` yielding `(tenant, bot)` and `drop_collection`-ing in teardown.
- **Slug-compliance**: tenant/bot names use `f"test_t_{uuid.uuid4().hex[:8]}"` and `f"test_b_{uuid.uuid4().hex[:8]}"` — both lowercase + alphanumeric + underscore + 10 chars + start with alpha → SLUG_REGEX-compliant (3–40 chars).
- **Teardown** wraps `drop_collection(...)` in `try/except Exception: pass` for best-effort cleanup; orphan collections are detectable by post-suite `client.get_collections()`.
- **Verification (Checkpoint H):**
  ```bash
  docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v
  ```
  Inside the web container (Qdrant resolves as `qdrant:6334`). All tests should pass. If any fail, inspect via `make shell`.
- **Rollback:** `rm tests/test_qdrant_collection.py`.

### Step 9 — Extend `scripts/verify_setup.py` with `--full`
- **Goal:** add an opt-in round-trip without breaking Phase 1's default behavior.
- **Files touched:** `scripts/verify_setup.py` (EXTEND — preserve `ping_postgres()` and `ping_qdrant()` verbatim; only `main()` is changed; `roundtrip_qdrant_collection()` is new).
- **Body:** verbatim from spec §"`scripts/verify_setup.py`": `argparse` with `--full`, `roundtrip_qdrant_collection()` with `tenant_id=f"verify_{int(time.time())}"`, `bot_id="rt"` (**SLUG INVALID — 2 chars**, must be ≥3 → use `bot_id="rt0"` instead, see §6 ambiguity #7). The round-trip creates → upserts a single point with all three vector types → delete-by-doc-id → drops in finally.
- **Verification (Checkpoint I):**
  ```bash
  docker compose -f docker-compose.yml exec web python scripts/verify_setup.py            # Phase 1 default mode — pre-existing behavior, exits 0 with both checks pass
  docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full     # round-trip exits 0
  ```
- **Rollback:** restore the Phase 1 version of `scripts/verify_setup.py` from git history (or copy from `build_prompts/phase_1_foundation/spec.md` §"`scripts/verify_setup.py`").

### Step 10 — Lint, format, full test suite + regression sweep
- **Goal:** ruff is clean, every test (Phase 1 + 2 + 3) green, /healthz still 200.
- **Commands:**
  ```bash
  uv run ruff check .                              # zero violations
  uv run ruff format --check .                     # zero changes needed
  uv run pytest -v                                 # full suite — Phase 1 healthz, Phase 2 models/naming, Phase 3 client tests; collection tests skip from host (no qdrant resolvable)
  docker compose -f docker-compose.yml exec web pytest -v   # full suite inside container, including Phase 3 collection tests against real Qdrant
  curl -fsS http://localhost:8080/healthz | python -m json.tool
  git status --short                               # only the 6 expected paths in the diff
  ```
- **Verification (Checkpoint J):** all three test runs green; healthz returns `{"status":"ok"}` with both components ok; `git status` shows exactly: `apps/qdrant_core/{exceptions,client,collection}.py`, `scripts/verify_setup.py`, `tests/{test_qdrant_client,test_qdrant_collection}.py`.
- **Rollback:** anything that fails — fix in place; never weaken ruff config or `--no-verify`.

### Step 11 — Implementation report
- **Goal:** the "When you finish" §"…short report" — files created, deviations, ambiguities, acceptance-criteria results.
- **Files touched:** `build_prompts/phase_3_qdrant_layer/implementation_report.md` (NEW).

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Detection |
|---|---|---|---|---|---|
| 1 | **`qdrant-client` API drift.** Spec sketches `KeywordIndexParams(is_tenant=True)`, `client.collection_exists`, `client.count(count_filter=)`, `MultiVectorConfig` import path — all may have shifted between the version pinned in `uv.lock` and what the spec assumes. | High | Critical (collection.py won't import or call signatures fail) | Step 1 inspects the installed API BEFORE writing collection.py. Adapt syntax, preserve semantics. | Checkpoint A; Checkpoint F's `python -c "from apps.qdrant_core.collection import ..."`. |
| 2 | **ColBERT vector size set to 128 instead of 1024.** Vanilla ColBERTv2 is 128/token; BGE-M3 is 1024/token. Phase 4 upserts would fail with dim mismatch. | Low (spec is explicit) | Critical (silent until Phase 4) | Constant `COLBERT_VECTOR_SIZE = 1024` + `assert COLBERT_VECTOR_SIZE == 1024`; integration test reads back `info.config.params.vectors["colbert"].size`. | Checkpoint G + Checkpoint H's `test_create_succeeds_with_locked_schema`. |
| 3 | **`HnswConfigDiff(m=0)` interpreted as default-m, not disabled.** Some Qdrant versions treat `m=0` as "use default 16". | Low (Qdrant 1.17 honors m=0) | Major (ColBERT becomes a 1024-dim-per-token first-stage index — huge memory) | Read back `info.config.params.vectors["colbert"].hnsw_config.m` after creation; assert `== 0`. | Checkpoint G manual + add to Checkpoint H. |
| 4 | **`is_tenant=True` API location wrong.** May not be on `KeywordIndexParams`; some versions place it on `create_payload_index(...)` directly. | Medium | Major (tenant-aware storage layout not enabled — perf-only impact, not correctness) | Step 1 verifies via `inspect.signature(KeywordIndexParams)` and `.signature(create_payload_index)`. Adapt while preserving the flag. | Checkpoint A + post-create inspection of `info.payload_schema['tenant_id']`. |
| 5 | **Singleton constructed pre-fork.** If anything in `apps/qdrant_core/__init__.py` (or anywhere imported at Django startup) calls `get_qdrant_client()`, gunicorn master holds a gRPC channel before forking → workers inherit broken state. | Low (spec is explicit lazy) | Major (workers crash on first request) | Module-import smoke at Checkpoint C uses `python -c "from ... import get_qdrant_client"` — does NOT call the function. Verify `apps/qdrant_core/__init__.py` is empty (Phase 1's stub) and stays empty. | `grep -rn "get_qdrant_client()" apps/qdrant_core/__init__.py` — should be empty. |
| 6 | **Retry decorator catches `BaseException`.** `except BaseException` would swallow `KeyboardInterrupt` and `SystemExit`, causing zombie processes. | Low (spec uses `except Exception`) | Major (un-killable workers) | Spec body uses `except Exception`. Verify by code review. | Step 4's `TestRetryDecorator` covers `Exception` subclasses; doesn't cover `BaseException`-only — flag for Phase 8. |
| 7 | **Concurrent `get_or_create_collection` race (two workers create simultaneously).** Worker A's `exists()` returns False, Worker B's same — both call `create_collection`, B gets HTTP 409. | Medium (Phase 5+'s upload pipeline could parallel-create) | Major (one worker raises an exception that propagates to the user) | Spec catches `UnexpectedResponse` with `status_code == 409`, verifies schema, returns the name. | Code review at Step 6; not directly tested in Phase 3 (would need racing workers). |
| 8 | **Test collections orphaned in Qdrant after a failed test.** `fresh_bot` fixture's teardown `try/except Exception: pass` swallows transient drop errors. | Medium (transient drop failure on a busy Qdrant) | Minor (operator manually drops test_t_*) | Best-effort drop is acceptable in v1; flag in *implementation report* a recommended cleanup script. Logging the drop failure (instead of silent pass) is a [minor] revision opportunity (see §6 #4). | Post-suite `client.get_collections()` should not list `test_t_*` (manual). |
| 9 | **Test slug names invalid.** `f"test_b_{uuid.hex[:8]}"` is 14 chars — slug-valid; but `bot_id="rt"` in the verify_setup script is 2 chars — INVALID. | High (spec literally has "rt") | Major (`verify_setup --full` always fails on slug regex) | §6 ambiguity #7: change to `bot_id="rt0"` (3 chars, slug-valid). | Checkpoint I would surface immediately. |
| 10 | **`scripts/verify_setup.py --full` runs but Qdrant unreachable.** Without a live Qdrant the round-trip silently appears to succeed if errors are swallowed. | Low | Major (false-green CI) | The round-trip raises `SystemExit` if any step fails (spec body's `if deleted != 1: raise SystemExit(...)`). Connection errors propagate from `create_collection_for_bot` via `with_retry` → `QdrantConnectionError`. | Checkpoint I. |
| 11 | **Phase 1/2 regression — accidentally edited a file outside Phase 3 scope.** | Low | Major (Phase 1 healthz or Phase 2 models break) | Step 10 `git status --short` lists only the 6 expected paths; `pytest` runs Phase 1's `test_healthz.py` and Phase 2's `test_models.py`/`test_naming.py` and confirms green. | Checkpoint J. |
| 12 | **Schema-mismatch diff dict not JSON-serializable.** `_compare_schema` builds `diff[key] = f"expected X, got {actual_value}"`; if `actual_value` is a `Distance` enum, f-string → `"Distance.COSINE"` (string). Should be safe, but a future change to use raw model objects would break. | Low | Minor (logs / serialization break only on schema-mismatch path) | Spec body f-strings ensure strings. Code review at Step 6. | Manual. |
| 13 | **`functools.lru_cache(maxsize=1)` doesn't survive `os.fork()` cleanly.** Forked workers re-evaluate `get_qdrant_client()` on first call. Desired behavior; flag the assumption. | N/A (works as desired) | N/A | Test fixture pattern (`get_qdrant_client.cache_clear()`) verifies fresh instance per call. | Step 4's `TestSingleton`. |
| 14 | **Two QdrantClient singletons (Phase 1's healthz + Phase 3's `get_qdrant_client`).** Phase 1's `apps/core/views.py` constructs its own (timeout=2 for healthz speed); Phase 3's uses timeout=10. Per-module caches; both are correct, but a code reader may be confused. | Low | Minor (cosmetic) | Document in implementation report; do NOT consolidate (would change healthz timeout semantic). | Code review. |
| 15 | **Tests run from host shell where Qdrant is on `localhost:6334`, not `qdrant:6334`.** `settings.QDRANT["HOST"]="qdrant"` only resolves inside the Compose network. Host pytest run would fail to connect → `qdrant_available` fixture skips, test_qdrant_collection.py reports `skipped` not `passed`. | High (host pytest is acceptance criterion 8) | Minor (acceptance #8 says "skip gracefully" or "pass if reachable" — both are PASS) | The `qdrant_available` fixture's `pytest.skip(...)` is the documented escape. Acceptance #8 explicitly accepts skip. | Step 10's host pytest. |
| 16 | **Logging cardinality on retry.** Every `with_retry` retry logs WARNING. In a transient outage with high request volume, logs flood. | Low (Phase 3 traffic is zero) | Minor (hardening concern for Phase 8) | Document in implementation report. v1 acceptable. | N/A. |
| 17 | **Implementer reaches for `uv add`** — violates spec hard constraint #2. | Low | Major (changes locked deps) | All needed packages already in `pyproject.toml`. If `uv add` is reached for, stop and re-read spec. | `git diff pyproject.toml uv.lock` should show no changes. |
| 18 | **Test parallelism (`pytest -n N`) untested.** | Low (default sequential) | Minor | Plan assumes sequential. If pytest-xdist is used, each worker forks its own process and lru_cache reinstantiates per worker — should be fine, but unverified. | Phase 8 test-perf review. |
| 19 | **API key rotation invalidates cached client.** `settings.QDRANT["API_KEY"]` is read once on first `get_qdrant_client()` call. A subsequent rotation requires worker restart. | Low | Minor | v1 deferral. Document in implementation report. | Operational. |
| 20 | **`lru_cache(maxsize=1)` on no-arg function — argument evolution risk.** If a future change adds a parameter to `get_qdrant_client()`, the cache becomes per-arg, not singleton. Currently no-args, so cache works as singleton. | N/A (defensive note) | N/A | Function signature stays no-args in Phase 3. Future phases must explicitly verify before adding args. | Code review. |

---

## 5. Verification checkpoints

| # | After step | Command | Expected |
|---|---|---|---|
| **A** | Step 1 (API inspect) | `python -c` import block + `inspect.signature(...)` calls (see Step 1) | All imports succeed; signatures captured for adapter notes. |
| **B** | Step 2 (exceptions) | `python -c "from apps.qdrant_core.exceptions import ...; print('ok')"` | `ok`. |
| **C** | Step 3 (client) | `python -c "from apps.qdrant_core.client import get_qdrant_client, with_retry, _is_transient; print('ok')"` (no call) | `ok`. |
| **D** | Step 4 (client tests) | `uv run pytest tests/test_qdrant_client.py -v` | All `TestSingleton`, `TestIsTransient`, `TestRetryDecorator` green. |
| **E** | Step 5 (stack up) | `make ps && make health` | Containers healthy; `/healthz` returns 200 with both components ok. |
| **F** | Step 6 (collection module imports) | `python -c "from apps.qdrant_core.collection import ...; assert COLBERT_VECTOR_SIZE==1024 and DENSE_VECTOR_SIZE==1024"` + `manage.py check` | `ok` + 0 issues. |
| **G** | Step 7 (Django-shell smoke) | shell session — see Step 7 commands | `dense.size=1024`, `colbert.size=1024`, `colbert.hnsw.m=0`, sparse `["bm25"]`, 8 payload indexes. |
| **H** | Step 8 (collection tests inside container) | `docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v` | All four test classes green. |
| **I** | Step 9 (verify_setup) | `… exec web python scripts/verify_setup.py` (default) + `… --full` | Both exit 0. |
| **J** | Step 10 (full suite + regression) | `uv run ruff check . && uv run ruff format --check . && uv run pytest -v && docker compose -f docker-compose.yml exec web pytest -v && make health && git status --short` | Ruff clean; full suite green host-side (with `test_qdrant_collection` skipping if Qdrant unreachable from host) and inside-container (all green); healthz 200; `git status` lists only the 6 expected paths. |

---

## 6. Spec ambiguities & open questions

### #1 — `qdrant-client` API drift (HIGH)
- **Ambiguity:** spec sketches a particular API surface; the installed version may differ.
- **Proposed call:** Step 1 inspects before writing. Adapt syntax, preserve semantics. Document each adaptation in the implementation report.
- **Reversibility:** trivial; each call site can be reshaped without changing function signatures or test assertions.

### #2 — `client.collection_exists(name)` may not exist
- **Ambiguity:** older `qdrant-client` versions surface this as `name in [c.name for c in client.get_collections().collections]`.
- **Proposed call:** prefer `collection_exists` if present (cleaner; one round-trip). Fall back to `get_collections()` filter if missing. Wrapper internal to `collection.py` — caller-facing helpers don't change.
- **Reversibility:** trivial.

### #3 — `client.count(count_filter=…)` parameter name
- **Ambiguity:** the kwarg may be `filter=` in some versions.
- **Proposed call:** use whichever Step 1 finds; `.count` attribute on the result is consistent across versions.
- **Reversibility:** trivial.

### #4 — `_compare_schema()` scope (HNSW m / ef_construct)
- **Ambiguity:** spec checks `dense.size`, `dense.distance`, `colbert.size`, `colbert.multivector` presence, `sparse.bm25.modifier`. Does NOT check HNSW `m` or `ef_construct`.
- **Proposed call:** keep spec's scope. HNSW params are tunable, not load-bearing for correctness — checking them would generate false positives if a future Phase 8 wants to retune. Document in implementation report.
- **Reversibility:** trivial — extend `_compare_schema` to add HNSW checks.

### #5 — `qdrant_available` fixture scope (session vs function)
- **Ambiguity:** spec uses session-scope. Faster (one connect per session); but if a single test corrupts the connection, all subsequent tests skip with the cached connection error.
- **Proposed call:** keep session-scope. The `cache_clear()` call inside the fixture re-instantiates the client; teardown isn't strictly needed. Function-scope would re-check Qdrant 15× per pytest run — not worth the cost.
- **Reversibility:** trivial — change `scope="session"` → `scope="function"`.

### #6 — `with_retry` retrying `drop_collection` mid-create race
- **Ambiguity:** Could `with_retry`'d `drop_collection` be invoked while another process is in the middle of creating the same collection?
- **Proposed call:** edge case is benign. Drop is idempotent; if creation isn't yet committed, drop is a no-op (returns False); the second create then succeeds. No data loss because there's no data yet.
- **Reversibility:** N/A.

### #7 — `bot_id="rt"` in `verify_setup.py --full` violates slug regex (CRITICAL)
- **Ambiguity:** spec body literally has `test_bot = "rt"` — 2 chars — but `SLUG_PATTERN = r"^[a-z0-9][a-z0-9_]{2,39}$"` requires 3+ chars. `validate_slug("rt")` raises.
- **Proposed call:** **change to `test_bot = "rt0"`** (3 chars, slug-valid). Documented as a spec defect. The 01_plan prompt's §6 explicitly raised this.
- **Reversibility:** trivial.

### #8 — `tests/test_qdrant_collection.py` running from host shell
- **Ambiguity:** host pytest sees `settings.QDRANT["HOST"]="qdrant"`, which doesn't resolve outside the Compose network. The fixture skips with a message.
- **Proposed call:** acceptance criterion #8 explicitly accepts skip-or-pass. No special host override needed. Implementation report documents that **host runs of `test_qdrant_collection.py` skip; container runs pass**.
- **Reversibility:** if a developer wants to run from host, they can `export QDRANT_HOST=localhost` before pytest. Documented in cheat-sheet.

### #9 — `info.payload_schema` exposure (test assertion)
- **Ambiguity:** the integration test `assert "doc_id" in info.payload_schema`. Some `qdrant-client` versions return a dict-like; others return a `PayloadSchemaInfo` object.
- **Proposed call:** if dict-like, the test passes verbatim. If object-attribute, adapt: `assert hasattr(info.payload_schema, "doc_id")` or wrap in `dict(info.payload_schema)`. Step 1 inspection should reveal.
- **Reversibility:** trivial.

### #10 — Two `QdrantClient` singletons (Phase 1 healthz + Phase 3 client)
- **Ambiguity:** Phase 1's `apps/core/views.py` has its own cached `QdrantClient(timeout=2)`; Phase 3's `get_qdrant_client` has `timeout=10`. Two singletons.
- **Proposed call:** keep separate. Healthz needs aggressive 2s timeout to never hang the load balancer; Phase 3's upserts need 10s. Consolidating would either (a) make healthz slow on partial degradation or (b) make upserts time out on large batches. Document in implementation report — it's intentional, not a bug.
- **Reversibility:** trivial — would change Phase 1 file (don't-touch list); deferred unless explicitly needed.

### #11 — `MultiVectorConfig` placement
- **Ambiguity:** spec puts `MultiVectorConfig` inside `VectorParams(multivector_config=...)`. Some `qdrant-client` versions require a separate `multivectors_config={"colbert": MultiVectorConfig(...)}` kwarg on `create_collection` instead.
- **Proposed call:** Step 1's positive-construction test (`VectorParams(..., multivector_config=...)`) confirms the inside-VectorParams form works. If construction fails, fall back to the top-level kwarg.
- **Reversibility:** trivial.

### #12 — `timeout=10` may be too short for Phase 5 large-batch upserts
- **Ambiguity:** Phase 3 sets the gRPC client timeout to 10 seconds. Phase 5's batch upserts (hundreds of chunks per doc) may exceed this.
- **Proposed call:** keep 10s for Phase 3 (round-trip is one point). Document for Phase 5's review — it may need to either bump the timeout or use per-call overrides.
- **Reversibility:** trivial — change in `client.py` or via per-call `timeout=` arg.

### #13 — `drop_collection` has no safety gate
- **Ambiguity:** any caller with a typo'd `tenant_id` could drop the wrong bot's collection, losing data.
- **Proposed call:** v1 acceptable — `drop_collection` is only called by tests and Phase 6's "delete entire bot" path (which arrives in v5 anyway). Phase 8's runbook should require `--confirm` flag in any operator CLI wrapper.
- **Reversibility:** trivial — add a confirmation arg later.

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope (per spec §"Out of scope for Phase 3"):
- BGE-M3 embedder (Phase 4)
- Chunker (Phase 4)
- DRF serializers for upload (Phase 5)
- POST `/v1/.../documents` view (Phase 5)
- Pipeline orchestrator (Phase 5)
- Postgres advisory-lock acquisition (Phase 5)
- DELETE endpoint (Phase 6)
- gRPC search service / `search.proto` (Phase 7)
- Hybrid search (Phase 7)
- Quantization (v4)
- Atomic version swap (v2)
- Audit log (v3)

### Phase 1 + Phase 2 don't-touch (≥23 files, all preserved):
- **Phase 1 (15):** `pyproject.toml`, `config/{settings,celery,urls,wsgi,asgi}.py`, `apps/core/{views,logging,urls,apps}.py`, `apps/core/__init__.py`, `Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`, `tests/{test_settings,conftest,test_healthz}.py`, `.env.example`. (Plus `manage.py`, `.python-version`, `.gitignore`, `.dockerignore`.)
- **Phase 2 (8 + migrations):** `apps/tenants/{validators,models,admin,apps}.py`, `apps/tenants/migrations/0001_initial.py`, `apps/documents/{models,admin,apps}.py`, `apps/documents/migrations/0001_initial.py`, `apps/qdrant_core/naming.py`, `tests/test_models.py`, `tests/test_naming.py`.
- **Interim infra:** `Makefile` (added between Phase 1 and Phase 3) — touched only as a runner of compose/pytest commands; not modified.

### Apps that stay untouched in Phase 3:
- `apps/ingestion/` (Phase 4)
- `apps/grpc_service/` (Phase 7)
- `proto/` directory (Phase 7)
- All Phase 1/2 `apps/<x>/__init__.py` and `apps.py` config files

### `git status --short` expectation after Step 10:
```
?? apps/qdrant_core/exceptions.py
?? apps/qdrant_core/client.py
?? apps/qdrant_core/collection.py
?? tests/test_qdrant_client.py
?? tests/test_qdrant_collection.py
 M scripts/verify_setup.py
?? build_prompts/phase_3_qdrant_layer/{plan,plan_review,implementation_report}.md
```
Anything else is a regression — `git checkout --` immediately.

---

## 8. Acceptance-criteria mapping

| # | Criterion (summary) | Step that satisfies | Verification command | Expected output |
|---|---|---|---|---|
| 1 | `ruff check .` zero violations | Step 10 | `uv run ruff check .` | `All checks passed!` |
| 2 | `ruff format --check .` passes | Step 10 | `uv run ruff format --check .` | `N files already formatted` |
| 3 | `pytest tests/test_qdrant_client.py -v` green (no Qdrant required) | Step 4 | `uv run pytest tests/test_qdrant_client.py -v` | All tests pass |
| 4 | `docker compose -f docker-compose.yml up -d` green; web healthy on 8080 | Step 5 | `make up && make ps && make health` | Containers healthy/running; healthz 200 |
| 5 | From web container shell: `create_collection_for_bot('verifyt','verifyb')` works; collection appears in `client.get_collections()` | Step 7 | `make shell` then run smoke; or `docker compose exec web python -c "..."` | `t_verifyt__b_verifyb` printed; collection listed |
| 6 | `python scripts/verify_setup.py --full` exits 0; test collection dropped | Step 9 | `… exec web python scripts/verify_setup.py --full` | exit 0; orphan collections absent |
| 7 | `pytest tests/test_qdrant_collection.py -v` green inside container | Step 8 | `… exec web pytest tests/test_qdrant_collection.py -v` | All green |
| 8 | `pytest tests/test_qdrant_collection.py -v` from host either green or skips gracefully | Step 10 | `uv run pytest tests/test_qdrant_collection.py -v` | Green if `localhost:6334` reachable; else `skipped` with clear message |
| 9 | Full suite (Phase 1 + 2 + 3) green | Step 10 | `uv run pytest -v` | All tests pass (collection tests skip from host) |
| 10 | `/healthz` regression: 200 with both components ok | Step 5 + Step 10 | `make health` | `{"status":"ok",...}` |

---

## 9. Tooling commands cheat-sheet

> **Local `.env` HTTP_PORT is 8080**, `POSTGRES_USER=aarav`, `POSTGRES_DB=qdrant_rag`. Adjust if your `.env` differs.
> **Phase 1 host-side blockers** (docker socket permission, port conflicts) — see Phase 1 implementation_report.md outstanding-issues. Don't re-debug.

```bash
# === Step 1: inspect installed qdrant-client API surface ===
uv run python -c "from qdrant_client import QdrantClient; print(sorted(m for m in dir(QdrantClient) if not m.startswith('_')))"
uv run python -c "from qdrant_client.models import KeywordIndexParams, MultiVectorConfig, MultiVectorComparator, Modifier, SparseIndexParams, SparseVectorParams, VectorParams, HnswConfigDiff, Distance, PayloadSchemaType, Filter, FieldCondition, MatchValue; print('ok')"
uv run python -c "from qdrant_client.http.exceptions import UnexpectedResponse; print('ok')"
uv run python -c "import inspect; from qdrant_client import QdrantClient; print(inspect.signature(QdrantClient.create_collection))"
uv run python -c "import inspect; from qdrant_client import QdrantClient; print(inspect.signature(QdrantClient.create_payload_index))"
uv run python -c "import inspect; from qdrant_client import QdrantClient; print(inspect.signature(QdrantClient.count))"
uv run python -c "import inspect; from qdrant_client.models import KeywordIndexParams; print(inspect.signature(KeywordIndexParams))"

# === Step 2-4: exceptions, client, client tests ===
uv run python -c "from apps.qdrant_core.exceptions import QdrantError, QdrantConnectionError, CollectionSchemaMismatchError, QdrantOperationError; print('ok')"
uv run python -c "from apps.qdrant_core.client import get_qdrant_client, with_retry, _is_transient; print('ok')"   # no call
uv run pytest tests/test_qdrant_client.py -v

# === Step 5: stack up (production mode) ===
make up && sleep 60 && make ps && make health

# === Step 6: collection module ===
uv run python -c "from apps.qdrant_core.collection import create_collection_for_bot, get_or_create_collection, delete_by_doc_id, drop_collection, COLBERT_VECTOR_SIZE, DENSE_VECTOR_SIZE; assert COLBERT_VECTOR_SIZE==1024 and DENSE_VECTOR_SIZE==1024; print('ok')"
uv run python manage.py check

# === Step 7: Django-shell manual smoke (inside web container) ===
make shell
# >>> from apps.qdrant_core.collection import create_collection_for_bot, drop_collection
# >>> from apps.qdrant_core.client import get_qdrant_client
# >>> name = create_collection_for_bot("smoket", "smokeb")
# >>> info = get_qdrant_client().get_collection(name)
# >>> print(info.config.params.vectors["dense"].size, info.config.params.vectors["colbert"].size, info.config.params.vectors["colbert"].hnsw_config.m)
# >>> drop_collection("smoket", "smokeb")

# === Step 8: integration tests (inside container) ===
docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v

# === Step 9: verify_setup ===
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full

# === Step 10: full suite + regression ===
uv run ruff check . && uv run ruff format --check .
uv run pytest -v                                                     # collection tests skip from host
docker compose -f docker-compose.yml exec web pytest -v              # all green inside container
curl -fsS http://localhost:8080/healthz | python -m json.tool
git status --short

# === Cleanup ===
make down
```

**Non-obvious choices:**

- **Step 1 is mandatory before Step 6.** Skipping it means writing collection.py against a hypothetical API. Cheap insurance.
- **The client smoke at Checkpoint C does NOT call `get_qdrant_client()`** — only imports. Calling it would attempt a network connection at this stage and could mask import-time issues with runtime errors.
- **Manual Django-shell smoke at Step 7** before automating in tests. If anything is wrong with the schema, the shell shows a dim-mismatch immediately without burning pytest startup time.
- **`make shell` lands inside the web container**, where `qdrant:6334` resolves. From the host, `localhost:6334` would be needed; the Makefile's `make shell` uses `docker compose exec`, so this is automatic.
- **Host-side `pytest tests/test_qdrant_collection.py` is expected to skip** (acceptance criterion #8 explicitly accepts `skipped`). Inside-container `pytest` is the canonical run. If `s` markers (skipped) appear in host pytest output for `test_qdrant_collection.py`, this is intentional — not a regression.
- **`git status --short`** is the regression tripwire — anything outside the 6 expected paths is a leak.

---

## 10. Estimated effort

| Step | Wall-clock | Notes |
|---|---|---|
| 1. API inspect | 5–10 min | `inspect.signature` + a few imports. Document each finding. |
| 2. exceptions.py | 5 min | Verbatim from spec; tiny module. |
| 3. client.py | 15–20 min | Verbatim spec body; main risk is the retry decorator's loop logic — read carefully. |
| 4. test_qdrant_client.py | 15–20 min | Verbatim from spec; `FakeRpcError` subclass needs `code()` method. |
| 5. Stack up | 5–10 min | If Compose blockers fire, +30 min for the user round-trip. |
| 6. collection.py | 30–45 min | Adapt to actual API surface from Step 1. Hot spot. |
| 7. Manual smoke | 10 min | Ten lines in Django shell. |
| 8. test_qdrant_collection.py | 30–40 min | Verbatim spec body; debug per-test on failure. |
| 9. verify_setup.py extension | 15 min | argparse plumbing + the round-trip body. **Don't forget `bot_id="rt0"` not "rt".** |
| 10. Lint, full suite, regression | 15–20 min | Ruff is fast; pytest depends on integration tests' Qdrant round-trips (~10–30 s each). |
| 11. Implementation report | 15 min | Final write-up. |
| **Total** | **3–4 hours** | Hot spots: Step 1 (API drift discovery) and Step 6 (adapting to it). Everything else is mechanical. |
