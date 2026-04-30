# Phase 5b — Implementation Plan (REVISED)

> Audience: the implementation agent (Prompt 3 of 3). Phase 5b extends the working Phase 5a upload pipeline with three idempotency/concurrency features. Read end-to-end before touching any file.

---

## 0. Revision notes

Post-review revisions vs. the initial plan, with cross-references to `plan_review.md`:

| # | Section | Change | Resolves |
|---|---|---|---|
| 1 | §3 Step 6/8 + §4 R16 | Chunk-cap test restructured: use 1 large item that produces > 5000 chunks via long content + small per-source-type size, OR mock `apps.ingestion.chunker.count_tokens` to a fast `len(text)//4` to keep test runtime under 5 s. Real-tokenizer path was ~250 s, blocking. | Finding 2 (critical) |
| 2 | §3 Step 3 + §4 R18 | `cursor.fetchone()` defensive read: `row = cursor.fetchone(); got = bool(row and row[0])` (handles `None` rare case). | Finding 3 (critical) |
| 3 | §3 Step 8 | EXPLICIT step: edit the existing Phase-5a `test_201_replace_existing` to use a different `content_hash` between the two POSTs, otherwise 5b's short-circuit will turn the second POST into 200 `no_change` instead of 201 `replaced`. | Finding 1 (major) |
| 4 | §3 Step 4 | `is_replace` lifecycle clarified: set after `existing` lookup, used only on the post-short-circuit path (replace branch's `delete_by_doc_id`, final UploadResult.status). | Finding 4 (major) |
| 5 | §3 Step 7 | Threaded test: explicit comment that `connections.close_all()` releases the WORKER thread's connection (Django's connection pool is thread-local). | Finding 5 (major) |
| 6 | §3 Step 8 | Add `test_409_retry_after_header` to test_upload.py: patches `apps.ingestion.pipeline.upload_lock` to raise `ConcurrentUploadError(retry_after=7)`; asserts `response["Retry-After"] == "7"` AND body shape. | Finding 6 (major) |
| 7 | §3 Step 4 | Logging on the no_change path emits INFO log with full context (`tenant_id, bot_id, doc_id, chunk_count, items_processed, status_code=200, elapsed_ms`). | Finding 11 (major) |
| 8 | §3 Step 6 | `test_no_change_when_content_hash_matches_and_chunks_exist` asserts `last_refreshed_at` actually moved forward. | Finding 13 (minor) |
| 9 | §3 Step 3 + §4 R17 | Don't assert `pg_advisory_unlock` return value — may return False if lock already released by connection close. | Finding 10 (minor) |
| 10 | §3 Step 7 + §4 R12 | `tests/test_locks.py` skip-not-fail on SQLite (`if connection.vendor != "postgresql": pytest.skip(...)`). | Finding 14 (minor) |

All 2 critical and 7 major findings resolved inline. 5 minor findings folded as clarity improvements.

---

## 1. Plan summary

Phase 5b layers three independent hardening features onto the Phase 5a pipeline: (1) a `content_hash` short-circuit that returns 200 `no_change` when the incoming hash matches the stored Document and `chunk_count > 0`; (2) a 5-second acquire timeout on the per-doc `pg_advisory_lock` via `pg_try_advisory_lock` polling, returning 409 `concurrent_upload` with a `Retry-After` header on contention; (3) a hard 5000-chunk-per-doc cap that raises 422 `too_many_chunks` BEFORE embedding. The riskiest part is the threaded concurrent-lock test in `tests/test_locks.py` — Postgres advisory locks are session-scoped, and a worker thread with a leaked connection holds the lock until the process exits and breaks subsequent tests; the fix is `connections.close_all()` in the worker's `finally`. Build verifies itself via (a) `tests/test_pipeline.py` with mocked embedder (~1 s, no model load), (b) `tests/test_locks.py` against the real Postgres in Compose (~3 s including the timeout-test wait), and (c) extended `tests/test_upload.py` running end-to-end against live Qdrant.

---

## 2. Build order & dependency graph

### Files (5 changed + 2 new test files = 7 total)

| # | Path | Status | Depends on | Step |
|---|---|---|---|---|
| 1 | `apps/documents/exceptions.py` | EXTEND (add `ConcurrentUploadError`, `DocumentTooLargeError`) | — | Step 2 |
| 2 | `apps/ingestion/locks.py` | MODIFY (replace blocking acquire with `pg_try_advisory_lock` poll loop, raise `ConcurrentUploadError` on timeout) | (1) | Step 3 |
| 3 | `apps/ingestion/pipeline.py` | MODIFY (add short-circuit BEFORE collection get_or_create, add chunk cap AFTER chunking, add `MAX_CHUNKS_PER_DOC = 5000`) | (1), (2), Phase 2's `apps.qdrant_core.naming.collection_name` | Step 4 |
| 4 | `apps/documents/views.py` | MINOR EXTEND (status code 200 vs 201 branch on `result.status == "no_change"`; `Retry-After` header on `ConcurrentUploadError`) | (1) | Step 5 |
| 5 | `tests/test_pipeline.py` | NEW (mocked embedder, mocked Qdrant — fast unit tests) | (3) | Step 6 |
| 6 | `tests/test_locks.py` | NEW (real Postgres, threaded acquire/timeout) | (2) | Step 7 |
| 7 | `tests/test_upload.py` | EXTEND (add 2 new tests: `test_200_content_hash_short_circuit`, `test_422_too_many_chunks`) | (3), (4) | Step 8 |

### Acyclic dependency graph

```
exceptions.py ──► locks.py ──► pipeline.py ──► views.py
                  │              │              │
                  │              ▼              ▼
                  ▼          test_pipeline.py   test_upload.py (extend)
              test_locks.py
```

All Phase 1/2/3/4 files unchanged. Phase 5a's serializers.py, urls.py, fixtures, and config/urls.py unchanged.

---

## 3. Build steps (sequenced)

Ten numbered steps. Each has **goal**, **files**, **verification**, **rollback**.

### Step 1 — Read & inventory

- **Goal:** confirm Phase 5a's deliverables are on disk and green; capture mtimes for the don't-touch audit.
- **Files touched:** none.
- **Verification:**
  ```bash
  ls apps/documents/         # exceptions, serializers, views, urls, models, admin, apps, __init__, migrations
  ls apps/ingestion/         # apps, __init__, embedder, chunker, payload, locks, pipeline
  grep -nE 'class .*Error' apps/documents/exceptions.py    # 5 classes from 5a
  grep -n 'pg_advisory_lock' apps/ingestion/locks.py       # blocking version from 5a
  grep -nE 'MAX_CHUNKS_PER_DOC|content_hash' apps/ingestion/pipeline.py    # not yet
  uv run python -m pytest tests/test_upload.py -v 2>&1 | tail -3   # 13 passed (Phase 5a baseline)
  ```
- **Rollback:** N/A.

### Step 2 — Extend `apps/documents/exceptions.py`

- **Goal:** add two new typed exceptions per spec §"File-by-file specification → exceptions.py (EXTEND)".
- **Files touched:** `apps/documents/exceptions.py`.
- **Append (do NOT rewrite the file):**
  ```python
  class ConcurrentUploadError(UploadError):
      http_status = 409
      code = "concurrent_upload"

      def __init__(
          self, message: str, retry_after: int = 5, details: dict | None = None
      ) -> None:
          super().__init__(message, details=details)
          self.retry_after = retry_after


  class DocumentTooLargeError(UploadError):
      http_status = 422
      code = "too_many_chunks"
  ```
- **Verification:**
  ```bash
  uv run python -c "
  from apps.documents.exceptions import ConcurrentUploadError, DocumentTooLargeError, UploadError
  assert ConcurrentUploadError.http_status == 409
  assert ConcurrentUploadError.code == 'concurrent_upload'
  assert DocumentTooLargeError.http_status == 422
  assert DocumentTooLargeError.code == 'too_many_chunks'
  e = ConcurrentUploadError('busy', retry_after=7, details={'k':'v'})
  assert e.retry_after == 7 and e.details == {'k':'v'}
  print('exceptions OK')
  "
  uv run ruff check apps/documents/exceptions.py
  ```
- **Rollback:** delete the two appended classes.

### Step 3 — Modify `apps/ingestion/locks.py` (try-acquire with timeout)

- **Goal:** replace the Phase 5a blocking acquire with `pg_try_advisory_lock` polling. Total wait budget is 5 s by default (`DEFAULT_ACQUIRE_TIMEOUT_S = 5.0`); `_POLL_INTERVAL_S = 0.05`. On timeout, raise `ConcurrentUploadError` BEFORE `yield`. Critical: the `acquired` flag tracks whether unlock should run (don't unlock a lock you never held — it's a no-op but emits noise).
- **Files touched:** `apps/ingestion/locks.py`.
- **Replacement:** verbatim from spec body. The kwarg `timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT_S` allows tests to use 1 s for the timeout-test.
- **Verification:**
  ```bash
  uv run python -c "
  from apps.ingestion.locks import upload_lock, DEFAULT_ACQUIRE_TIMEOUT_S
  assert DEFAULT_ACQUIRE_TIMEOUT_S == 5.0
  print('locks import OK')
  "
  uv run ruff check apps/ingestion/locks.py
  # Phase 5a regression: locks still acquire+release in normal path
  uv run python -m pytest tests/test_models.py -q   # ~38 tests, confirms Postgres still reachable for ORM
  ```
- **Rollback:** restore Phase 5a's locks.py from build_prompts/phase_5a_upload_core/spec.md or memory.

### Step 4 — Modify `apps/ingestion/pipeline.py` (short-circuit + chunk cap)

- **Goal:** insert two new gates around Phase 5a's existing pipeline body, per the locked order in spec §"Hard constraints" #9. `MAX_CHUNKS_PER_DOC = 5000` sits at module level.
- **Files touched:** `apps/ingestion/pipeline.py`.
- **Edits (in order):**
  1. **Imports**: add `from apps.documents.exceptions import DocumentTooLargeError` (ConcurrentUploadError comes via locks.py — pipeline doesn't catch it explicitly because the view does); add `from apps.qdrant_core.naming import collection_name as derive_collection_name`. Module-level constant: `MAX_CHUNKS_PER_DOC = 5000`.
  2. **Move the `existing` lookup BEFORE the `get_or_create_collection` call** so we can short-circuit without touching Qdrant on the no_change path. Phase 5a does this lookup AFTER `get_or_create_collection`; Phase 5b moves it earlier.
  3. **Insert the short-circuit gate** between Tenant/Bot get_or_create and the collection ensure call:
     ```python
     existing = Document.objects.filter(doc_id=doc_id).first()
     is_replace = existing is not None
     incoming_hash = (body.get("content_hash") or "").strip()
     if (
         existing
         and existing.chunk_count > 0
         and incoming_hash
         and existing.content_hash == incoming_hash
     ):
         if existing.tenant_id != tenant_id or existing.bot_id != bot_id:
             raise QdrantWriteError(
                 "doc_id collision across tenants/bots — refuse to short-circuit.",
                 details={"doc_id": doc_id, "expected_tenant": tenant_id, "found_tenant": existing.tenant_id},
             )
         existing.save(update_fields=["last_refreshed_at"])
         logger.info("upload_no_change", extra={...})
         return UploadResult(
             doc_id=doc_id,
             chunks_created=existing.chunk_count,
             items_processed=existing.item_count,
             collection_name=derive_collection_name(tenant_id, bot_id),
             status="no_change",
         )
     ```
  4. **Move** the cross-tenant collision check inside the short-circuit branch (above) AND keep it on the replace branch (Phase 5a already had it). The check appears in both places now.
  5. **Insert chunk-cap gate** AFTER the per-item chunking loop (`flat` is fully populated):
     ```python
     if len(flat) > MAX_CHUNKS_PER_DOC:
         raise DocumentTooLargeError(
             f"Document produces {len(flat)} chunks, max is {MAX_CHUNKS_PER_DOC}",
             details={"chunk_count": len(flat), "max": MAX_CHUNKS_PER_DOC},
         )
     ```
  6. **Status field** in the existing UploadResult return at the end is unchanged ("created" or "replaced"); the no_change status is set inside the short-circuit branch only.
- **Verification:**
  ```bash
  uv run python manage.py check
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from apps.ingestion.pipeline import UploadPipeline, MAX_CHUNKS_PER_DOC
  assert MAX_CHUNKS_PER_DOC == 5000
  print('pipeline OK; MAX_CHUNKS_PER_DOC=', MAX_CHUNKS_PER_DOC)
  "
  uv run ruff check apps/ingestion/pipeline.py
  ```
- **Rollback:** revert the file.

### Step 5 — Minor extend `apps/documents/views.py`

- **Goal:** branch the success status code (200 vs 201) on `result.status`; add a `Retry-After` header on `ConcurrentUploadError` 409 responses.
- **Files touched:** `apps/documents/views.py`.
- **Edits:**
  1. After `UploadPipeline.execute(...)` success: `status_code = 200 if result.status == "no_change" else 201` and pass `status=status_code` to `Response(...)`.
  2. In the `except UploadError` handler, after building the response: if `isinstance(exc, ConcurrentUploadError)`, set `response["Retry-After"] = str(exc.retry_after)` before returning.
- **Verification:**
  ```bash
  uv run python manage.py check
  uv run ruff check apps/documents/views.py
  ```
- **Rollback:** revert the file.

### Step 6 — `tests/test_pipeline.py` (NEW)

- **Goal:** unit tests for the pipeline's short-circuit + chunk-cap gates. Mocks `embed_passages`, `get_qdrant_client`, `get_or_create_collection`, `delete_by_doc_id`. The lock context manager is the real one OR autouse-patched to no-op (same as Phase 5a's `tests/test_upload.py` since the test settings still use SQLite).
- **Files touched:** `tests/test_pipeline.py`.
- **Tests (per spec):**
  - `TestContentHashShortCircuit::test_no_change_when_content_hash_matches_and_chunks_exist` — first POST creates, second POST short-circuits; embed_passages called exactly once.
  - `TestContentHashShortCircuit::test_full_pipeline_when_content_hash_differs` — different hashes → both run full pipeline; embed_passages called twice.
  - `TestContentHashShortCircuit::test_full_pipeline_when_content_hash_absent` — empty hash → no short-circuit possible.
  - `TestChunkCap::test_too_many_chunks_raises_document_too_large` — 5001 FAQ items → DocumentTooLargeError; client.upsert NOT called.
  - `TestNoEmbeddableContent::test_all_empty_content_raises` — whitespace-only content → NoEmbeddableContentError.
- **Critical detail:** since pipeline.py uses `pg_advisory_lock` (real SQL), and the test settings use SQLite, the test_pipeline.py file MUST autouse-patch `apps.ingestion.pipeline.upload_lock` to no-op (same trick as Phase 5a's test_upload.py).
- **Verification:**
  ```bash
  uv run python -m pytest tests/test_pipeline.py -v
  ```
- **Rollback:** delete the file.

### Step 7 — `tests/test_locks.py` (NEW)

- **Goal:** verify the real `pg_advisory_lock` semantics against real Postgres. `@pytest.mark.django_db(transaction=True)` because we need committed transactions visible across threads.
- **Files touched:** `tests/test_locks.py`.
- **Tests (per spec):**
  - `test_acquire_and_release` — basic context manager.
  - `test_concurrent_acquire_blocks` — main holds lock; worker thread with `timeout_s=1.0` times out; result["b"] == "timeout".
  - `test_different_keys_dont_block` — main holds doc-1; worker with doc-different acquires; result["b"] == "acquired".
- **Critical detail (Pitfall #3):** worker's `finally` block MUST call `connections.close_all()` to release the worker's DB connection. Without this, the lock leaks for ~CONN_MAX_AGE seconds and breaks subsequent tests.
- **SQLite-vs-Postgres:** these tests need real Postgres. Skip-not-fail if Postgres unreachable. **Alternative:** put a `pytest.skip` at module level if `connection.vendor != "postgresql"` (SQLite test_settings would skip cleanly).
- **Verification:**
  ```bash
  # Host-equivalent — won't reach Postgres because tests/test_settings.py uses SQLite.
  # Tests should skip gracefully OR use a custom marker that requires postgres.
  uv run python -m pytest tests/test_locks.py -v
  ```
  Expected: skipped with "requires PostgreSQL" message OR pass if real Postgres connection somehow available. Inside container with config.settings → real Postgres → tests run.
- **Rollback:** delete the file.

### Step 8 — Extend `tests/test_upload.py`

- **Goal:** append three new tests AND fix one existing test to keep working post-5b.
- **Files touched:** `tests/test_upload.py`.
- **Edit existing test:** `test_201_replace_existing` currently POSTs the same body twice (`valid_pdf_doc.json` has `content_hash: "sha256:a3f8c4b1e2"`). After 5b's short-circuit lands, the second POST will return 200 `no_change` instead of 201 `replaced`. Fix: between the two POSTs, set `body["content_hash"] = "sha256:second-different"` (or `del body["content_hash"]`) so the replace path still fires. Preserve the test's intent.
- **Append three new tests:**
  - `test_200_content_hash_short_circuit` — POST with `content_hash="sha256:matching"` twice; second returns 200 with `status="no_change"`.
  - `test_422_too_many_chunks` — single large item that produces > 5000 chunks; assert 422 with `code="too_many_chunks"`. **Constructed to run fast:** use `source_type="faq"` (200 tokens / chunk) and a single item with very long content (~500 KB of repeated text). Chunker produces ~2500 chunks per 1 MB on FAQ config; aim for > 5000 chunks in < 10 s tokenizer time. Alternative if too slow: patch `apps.ingestion.chunker.count_tokens` to `lambda t: max(1, len(t)//4)` for this test only — keeps the cap-fires assertion intact while skipping slow tokenizer calls. (Resolution applied in implementation.)
  - `test_409_retry_after_header` — patch `apps.ingestion.pipeline.upload_lock` (or the import inside the view's call chain) to raise `ConcurrentUploadError("busy", retry_after=7)`. Assert response.status_code == 409, body["error"]["code"] == "concurrent_upload", and `response["Retry-After"] == "7"`.
- **Verification:**
  ```bash
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
      uv run python -m pytest tests/test_upload.py -v
  ```
  Expected: 13 prior tests (one edited) + 3 new = 16/16 green.
- **Rollback:** revert the edits + delete the appended tests.

### Step 9 — Quality gates + manage.py check

- **Goal:** prove the project is structurally sound.
- **Commands:**
  ```bash
  uv run python manage.py check
  uv run python manage.py makemigrations --check --dry-run
  uv run ruff check .
  uv run ruff format --check .
  ```
- **Rollback:** N/A.

### Step 10 — Full regression + don't-touch audit + report

- **Goal:** prove all 101 prior tests + 5b's new tests are still green; mtime audit confirms only Phase 5b files changed.
- **Commands:**
  ```bash
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge uv run python -m pytest -v
  curl -fsS http://localhost:8080/healthz | python -m json.tool
  # Mtime audit (no git in repo)
  stat -c '%y %n' \
      apps/{core,tenants}/* \
      apps/qdrant_core/* \
      apps/ingestion/{embedder,chunker,payload,apps,__init__}.py \
      apps/documents/{models,admin,serializers,urls,apps,__init__}.py \
      config/{settings,urls,wsgi,asgi,celery,__init__}.py \
      tests/{conftest,test_settings,test_healthz,test_models,test_naming,test_qdrant_client,test_qdrant_collection,test_chunker,test_payload,test_embedder,__init__}.py \
      Dockerfile docker-compose.yml docker-compose.override.yml Makefile manage.py pyproject.toml uv.lock scripts/verify_setup.py
  ```
  All listed files must show pre-Phase-5b mtimes (≤ Phase 5a session end ≈ 13:54 today).
- **Implementation report** at `build_prompts/phase_5b_upload_idempotency/implementation_report.md`.

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Detection |
|---|---|---|---|---|---|
| R1 | Short-circuit incorrectly triggers on empty/missing `content_hash` | Medium | False no_change responses; data not refreshed even though caller resubmitted | Strict guard: `incoming_hash` must be non-empty AND `existing.content_hash == incoming_hash`; both `.strip()` applied | Test `test_full_pipeline_when_content_hash_absent` |
| R2 | `update_fields=["last_refreshed_at"]` doesn't trigger `auto_now` | Low | last_refreshed_at stays stale; no data corruption but wrong telemetry | Per Django docs, `auto_now=True` updates the field even with `update_fields=[...]`. Verify in test by reading `last_refreshed_at` before + after. | Test reads field timestamp delta |
| R3 | `pg_try_advisory_lock` mistakenly used as `pg_advisory_lock` (or vice-versa) | Medium | Pipeline hangs forever instead of returning 409 (or returns 409 immediately without trying to wait) | Spec body uses `pg_try_advisory_lock` in poll loop; implementation must match exactly. Ruff doesn't catch this — add a grep guard | `grep -c 'pg_try_advisory_lock' apps/ingestion/locks.py` ≥ 1 |
| R4 | Threaded test leaks DB connection → lock not released | High if forgotten | Subsequent tests fail mysteriously; CI flake | Worker's `finally: connections.close_all()`. Spec test body includes this. | Run test_locks.py twice in a row — both green |
| R5 | `MAX_CHUNKS_PER_DOC=5000` rejects legitimate large docs | Low (5000 chunks is generous; ~5000 × 600 tokens = 3M tokens of doc text) | False positive on borderline-large docs | Document the cap in API contract; v2 may make it per-tenant configurable | Phase 8 monitoring |
| R6 | Short-circuit happens BEFORE `get_or_create_collection`, so first upload to a NEW bot but with existing-doc_id wouldn't have a collection. Won't happen because `existing` only set if Document row exists, which only happens after a prior upload that created the collection. | Very low | Logic edge case | Logic confirmed: existing → previous upload happened → collection exists | Test reads collection_name from no_change result |
| R7 | DRF `Response` doesn't accept `response["Header"] = "value"` | Low (it does — inherits from Django HttpResponse) | Retry-After header missing | Verify in Step 5 with `assert response['Retry-After']` in a test | Test asserts header presence on 409 |
| R8 | Phase 5a's existing `test_201_replace_existing` (no `content_hash`) accidentally triggers 5b's short-circuit | Low | Phase 5a regression | 5a's fixture body has `"content_hash": "sha256:a3f8c4b1e2"`. Two consecutive POSTs with same hash WILL now trigger short-circuit → second returns 200 not 201. **This is a behavior change to the existing test.** | Verify: run `tests/test_upload.py::test_201_replace_existing` after 5b lands; if it fails, **modify the test** to use a different hash on the second POST OR remove content_hash from the body |
| R9 | Tenant.get_or_create runs BEFORE the short-circuit | Acceptable | First-contact creates Tenant/Bot rows even on "no_change" — but this only happens if the doc_id was already there, which means a prior upload already created the rows | N/A — by construction the rows exist | N/A |
| R10 | 5001-item FAQ chunk-cap test runs slow (~10 s due to many tokenizer calls) | Acceptable | Slower CI iteration | If too slow, reduce content per item to "Q? A." (very short → 1 chunk per item, fast tokenizer) | Spec already uses short content |
| R11 | `tests/test_pipeline.py` short-circuit test depends on `Document.objects.update_or_create` actually saving a row that the SECOND call can read. Pytest-django's transaction rollback discards data BETWEEN tests but not WITHIN a single test. | Acceptable | Test fails if rollback happens between calls | Each test issues both calls within the same `with django_db` scope (inside the same test function) | Test methodology |
| R12 | `tests/test_locks.py` requires real Postgres but pytest's `tests.test_settings` uses SQLite | High | Tests fail on host run | Skip-not-fail at module level: check `connection.vendor` and `pytest.skip("requires PostgreSQL")` if SQLite. Tests run when invoked from `docker compose exec web pytest tests/test_locks.py` (config.settings → Postgres). | Module-level skip + docs |
| R13 | Pipeline's `existing.tenant_id != tenant_id` check now appears in TWO places (short-circuit + replace). Code duplication. | Low | Maintenance burden | Acceptable v1; could extract a helper in v2 | N/A |
| R14 | Threaded `test_concurrent_acquire_blocks` deadlocks if main thread's lock release happens in `__exit__` ordering vs the worker thread's polling | Low | Test hangs | Set `t.join(timeout=3.0)` so the test fails-fast after 3s instead of hanging forever | Spec already has the join timeout |
| R15 | `connections.close_all()` in worker's finally runs before Postgres has actually unlocked the row, allowing flake | Low | Test fails ~1% | Postgres advisory unlock is synchronous; close_all happens after | Test stability via running multiple times |

---

## 5. Verification checkpoints

| # | Where | Command | Expected |
|---|---|---|---|
| V1 | After Step 2 (exceptions extension) | Import smoke + class attribute assertions | OK |
| V2 | After Step 3 (locks.py modification) | Import smoke + Phase 2 ORM regression | locks import OK; Phase 2 tests still pass |
| V3 | After Step 3 | `grep -c 'pg_try_advisory_lock' apps/ingestion/locks.py` | ≥ 1 |
| V4 | After Step 4 (pipeline modification) | manage.py check + `MAX_CHUNKS_PER_DOC == 5000` import smoke | OK |
| V5 | After Step 5 (views minor extend) | manage.py check | OK |
| V6 | After Step 6 (test_pipeline.py) | `pytest tests/test_pipeline.py -v` | All green; <2 s |
| V7 | After Step 7 (test_locks.py) | `pytest tests/test_locks.py -v` | All green (real Postgres) OR skipped with clear message (SQLite) |
| V8 | After Step 8 (test_upload.py extend) | `pytest tests/test_upload.py -v` | 15 tests passing (13 prior + 2 new) |
| V9 | After Step 9 (quality gates) | `manage.py check`, `ruff check`, `ruff format --check`, `makemigrations --check --dry-run` | All clean |
| V10 | After Step 10 (full regression) | Full `pytest -v` + `make health` + mtime audit | All green; healthz JSON; only Phase 5b files modified |

---

## 6. Spec ambiguities & open questions

1. **`update_fields=["last_refreshed_at"]` and `auto_now`.** Per Django docs, `auto_now=True` fields update on every `.save()` call AS LONG AS the field is in `update_fields` (or `update_fields` is None). Plan explicitly includes the field. Verify via test: read timestamp before save, sleep 0.01 s, save, read again, assert delta.
2. **`save(update_fields=[...])` doesn't update `chunk_count`.** Intentional — short-circuit doesn't change chunks; only `last_refreshed_at` ticks. ✓
3. **`ConcurrentUploadError` propagation through `with upload_lock(...)`.** The error is raised INSIDE the `cursor` context but BEFORE `yield`, so it propagates out of the `with upload_lock(...)` statement. The pipeline's caller (the view) catches `UploadError` and dispatches to status code 409. Verify by inspection of the locks.py spec body — the `raise` is inside `while True:` before any `yield`.
4. **5001-item chunk cap test runtime.** Each item produces ~1 chunk via the `faq` chunker. 5001 chunks × tokenizer call per chunk = potentially ~10 s. Acceptable for v1. If too slow in CI, mark with `@pytest.mark.slow` (not registered as a marker yet — would need pyproject update; defer).
5. **`tests/test_locks.py` connection close.** `connections.close_all()` releases the worker thread's DB connection and the session-level advisory lock with it. Without this, the lock leaks for `CONN_MAX_AGE=60s`. Spec includes this.
6. **`Retry-After` header on DRF `Response`.** DRF's `Response` is a subclass of Django's `SimpleTemplateResponse`/`HttpResponse`; setting `response["Retry-After"] = "5"` works. Verify with a runtime test in test_upload.py (could add `assert resp["Retry-After"] == "5"` on the 409 path — but Phase 5b's tests don't include that explicit assertion; flag for review).
7. **`derive_collection_name` import alias.** Phase 4's `apps.ingestion.payload.build_chunk_id` derives chunk_id; Phase 2's `apps.qdrant_core.naming.collection_name` derives collection name. Pipeline imports the latter under an alias to avoid shadowing the local variable `collection_name`.
8. **Phase 5a's `test_201_replace_existing` and the new short-circuit.** Phase 5a's `valid_pdf_doc.json` has `content_hash: "sha256:a3f8c4b1e2"`. Two consecutive POSTs with the same body will now trigger the 5b short-circuit (same hash → 200 no_change), NOT the 5a replace path (201 replaced). **This is a regression risk** — the test assertion `assert r2.json()["status"] == "replaced"` would fail. **Mitigation:** before Phase 5b changes, modify `test_201_replace_existing` to mutate the body between calls (e.g., change `content_hash` to `"sha256:a3f8c4b1e2-second"` for the second POST) so the replace path still fires. **Spec ambiguity #8** — confirm this fix is acceptable; it preserves test intent (replace path) while adapting to the new short-circuit.

---

## 7. Files deliberately NOT created / NOT modified

- **All Phase 1/2/3/4 files** — verified via mtime audit in Step 10.
- **Phase 5a files NOT touched in 5b:** `apps/documents/serializers.py`, `apps/documents/urls.py`, `config/urls.py`, `apps/documents/models.py`, `apps/documents/admin.py`, `apps/documents/migrations/*`, all three Phase 5a fixtures (`valid_pdf_doc.json`, `invalid_no_items.json`, `invalid_empty_content.json`).
- **No new fixtures** — 5b reuses 5a's via fixture mutation in tests.
- **Out of scope (per spec):**
  - DELETE endpoint (Phase 6)
  - Atomic version swap (v2)
  - gRPC search (Phase 7)
  - Audit log (v3)

---

## 8. Acceptance-criteria mapping

| # | Criterion | Build step | Verification | Expected |
|---|---|---|---|---|
| 1 | `uv run ruff check .` zero violations | Steps 2–8 + Step 9 | `uv run ruff check .` | `All checks passed!` |
| 2 | `uv run ruff format --check .` zero changes | Same | `uv run ruff format --check .` | `N files already formatted` |
| 3 | `pytest tests/test_pipeline.py -v` green | Step 6 | host-side or container | All tests pass; <5 s |
| 4 | `pytest tests/test_locks.py -v` green or skip | Step 7 | host (SQLite skip) OR container (Postgres pass) | Either path is valid per spec |
| 5 | Container `pytest tests/test_upload.py -v` green | Step 8 | docker compose exec | 15 tests pass (13 + 2) |
| 6 | Manual smoke: 201 → 200 no_change with same content_hash | Step 5 + Step 8 | curl twice with `content_hash` set | First 201; second 200 with `status="no_change"` |
| 7 | Phase 5a tests still green | Step 8 + Step 10 | container or host pytest | All 13 (or 15 after extend) tests pass |
| 8 | Full regression + healthz | Step 10 | full pytest + curl | 101 + 5b's tests green; healthz JSON |
| 9 | Only Phase 5b files modified | Step 10 | mtime audit (no git) | Only authorized files newer than session start |
| 10 | `make health` green JSON | Step 10 | curl /healthz | Documented JSON |

---

## 9. Tooling commands cheat-sheet

```bash
# ── Per-step verification ──
uv run python -c "..."                     # smoke imports
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run ruff check . && uv run ruff format --check .

# ── Phase 5b new tests ──
uv run python -m pytest tests/test_pipeline.py -v
uv run python -m pytest tests/test_locks.py -v
QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
    uv run python -m pytest tests/test_upload.py -v

# ── Full suite ──
QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
    uv run python -m pytest -v

# ── Inside container (canonical, when docker unblocked) ──
docker compose -f docker-compose.yml exec web pytest tests/test_pipeline.py -v
docker compose -f docker-compose.yml exec web pytest tests/test_locks.py -v
docker compose -f docker-compose.yml exec web pytest -v

# ── Manual smoke ──
DOC_ID=$(uuidgen)
sed "s/^{/{\"doc_id\":\"$DOC_ID\",\"content_hash\":\"sha256:fixed\",/" \
    tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @/tmp/with-id.json -w "\n%{http_code}\n"   # 201 first time
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @/tmp/with-id.json -w "\n%{http_code}\n"   # 200 no_change second time

# ── Health check ──
make health
curl -fsS http://localhost:8080/healthz | python -m json.tool
```

---

## 10. Estimated effort

| Step | Task | Effort |
|---|---|---|
| 1 | Read & inventory | 5 min |
| 2 | exceptions.py extension | 5 min |
| 3 | locks.py modification | 10 min |
| 4 | pipeline.py modification | 20 min (move existing block + add 2 gates + import alias) |
| 5 | views.py minor extend | 10 min |
| 6 | test_pipeline.py | 20 min |
| 7 | test_locks.py | 15 min |
| 8 | test_upload.py extend | 10 min |
| 9 | Quality gates | 5 min |
| 10 | Full regression + audit + report | 20 min |
| | **Total** | **~2 hours wall clock** |

---

## End of plan

Phase 6 (DELETE endpoint) is unblocked once Phase 5b ships green. Phase 6 reuses the Phase 5a-shipped pipeline's `delete_by_doc_id` helper.
