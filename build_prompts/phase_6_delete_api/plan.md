# Phase 6 — Implementation Plan (REVISED)

> Audience: the implementation agent (Prompt 3 of 3). Phase 6 adds the DELETE endpoint that completes the document lifecycle alongside Phase 5's upload. Read end-to-end before touching any file.

---

## 0. Revision notes

Post-review revisions vs. the initial plan, with cross-references to `plan_review.md`:

| # | Section | Change | Resolves |
|---|---|---|---|
| 1 | §3 Step 7 + §4 R13 | `test_204_delete_existing_doc` and `test_204_idempotent_redelete` use `assert not response.content` (Pythonic falsy check) instead of strict `== b""` for DRF version portability. | Finding 2 (critical) |
| 2 | §6 ambiguities + §3 Step 3 | Lock-then-lookup ordering explicitly justified: alternative (lookup-then-lock) is racy. Pipeline acquires `upload_lock` BEFORE `Document.objects.filter(...).first()`. | Finding 3 (major) |
| 3 | §6 ambiguities | Documented: soft-deleted Document → re-uploaded via Phase 5a → re-activates (status returns to ACTIVE, chunks re-upserted). Intentional v1 behavior. | Finding 4 (major) |
| 4 | §3 Step 7 | `test_404_cross_tenant_doc_id` docstring clarifies: tenant_b is never auto-created (DELETE doesn't auto-create); the cleanup `drop_collection` is defensive. | Finding 5 (major) |
| 5 | §6 ambiguities | 404 message stays generic — `"Document {doc_id} not found."` — same string whether tenant exists or not. Avoids existence leak. | Finding 6 (major) |
| 6 | §3 Step 3 | Pipeline lookup uses `Document.objects.filter(doc_id=doc_id).first()` with NO status filter — soft-deleted rows ARE returned, enabling idempotent re-delete (returns 204, sets `was_already_deleted=True`). | Finding 1 (major) |
| 7 | §3 Step 4 | View emits final INFO log on success (`delete_succeeded_response` with `tenant_id, bot_id, doc_id, chunks_deleted, was_already_deleted, status_code=204, elapsed_ms`). On failure, ERROR log with `exc_info=True`. | Finding 8 (major) |
| 8 | §6 ambiguities | `<uuid:doc_id>` is case-insensitive; view receives canonical-lowercase `uuid.UUID`. No special handling. | Finding 7 (minor) |
| 9 | §3 Step 7 | DRF Response.headers via `response["Retry-After"] = "5"` matches Phase 5b pattern; verified. | Finding 9 (minor) |
| 10 | §4 R | `delete_by_doc_id` is atomic per Qdrant's filter-delete semantics; all-or-nothing per doc_id. | Finding 11 (minor) |

All 1 critical and 6 major findings resolved inline. 5 minor findings folded as clarity improvements.

---

## 1. Plan summary

Phase 6 adds `DELETE /v1/tenants/<tenant_id>/bots/<bot_id>/documents/<uuid:doc_id>` — soft-delete in Postgres (`status="deleted"`, `chunk_count=0`) plus hard-delete in Qdrant via Phase 3's `delete_by_doc_id`. Most of the logic is composition of existing primitives: Phase 5b's `upload_lock` (5s timeout) for concurrency safety, Phase 2's `validate_slug` for URL slug validation, Phase 3's `delete_by_doc_id` for the Qdrant side. The riskiest piece is the cross-tenant `doc_id` collision case — a row found by doc_id whose tenant/bot doesn't match the URL params must return 404 (not leaked existence, not 500); the spec mandates an explicit tenant/bot match check after the .first() lookup. Build verifies itself via (a) `manage.py check` after each module is written (URL converter `<uuid:>` resolves correctly), (b) `tests/test_delete.py` against live Qdrant covering all 9 spec'd cases, and (c) manual curl smoke for the 201 → 204 → 204 (idempotent) → 404 → 400 lifecycle.

---

## 2. Build order & dependency graph

### Files (4 changed + 1 new = 5 total)

| # | Path | Status | Depends on | Step |
|---|---|---|---|---|
| 1 | `apps/documents/exceptions.py` | EXTEND (add `DocumentNotFoundError`) | Phase 5a's `UploadError` base | Step 2 |
| 2 | `apps/ingestion/pipeline.py` | EXTEND (add `DeleteResult` + `DeletePipeline` class) | (1), Phase 5b's `upload_lock`, Phase 3's `delete_by_doc_id`, Phase 2's `Document` | Step 3 |
| 3 | `apps/documents/views.py` | EXTEND (add `DeleteDocumentView`) | (2), Phase 5b's `ConcurrentUploadError`, Phase 2's `validate_slug`, existing `_error_response` helper | Step 4 |
| 4 | `apps/documents/urls.py` | EXTEND (add DELETE route) | (3) | Step 5 |
| 5 | `tests/test_delete.py` | NEW (9 tests) | (1), (2), (3), (4); Phase 5a's `valid_pdf_doc.json` fixture | Step 7 |

### Acyclic dependency graph

```
exceptions.py ──► pipeline.py ──► views.py ──► urls.py
                                       │            │
                                       └────────────┴──► test_delete.py
                                                            │
                                                            └── reuses fixtures/valid_pdf_doc.json
```

All Phase 1/2/3/4 files unchanged. Phase 5a's `serializers.py`, `models.py`, fixtures, and `config/urls.py` unchanged. Phase 5b's `locks.py` reused as-is.

---

## 3. Build steps (sequenced)

Nine numbered steps. Each has **goal**, **files**, **verification**, **rollback**.

### Step 1 — Read & inventory

- **Goal:** confirm Phase 5b deliverables on disk + capture mtimes for the don't-touch audit.
- **Files touched:** none.
- **Verification:**
  ```bash
  ls apps/documents/                          # exceptions, serializers, views, urls, models, admin, apps, __init__, migrations
  ls apps/ingestion/                          # apps, __init__, embedder, chunker, payload, locks, pipeline
  grep -nE 'class .*Error' apps/documents/exceptions.py    # 7 classes from 5a+5b (UploadError, InvalidPayloadError, NoEmbeddableContentError, QdrantWriteError, EmbedderError, ConcurrentUploadError, DocumentTooLargeError)
  grep -n 'upload_lock' apps/ingestion/pipeline.py          # already imported from Phase 5
  grep -nE 'DeletePipeline|DocumentNotFoundError' apps/  -r  # NOT yet
  uv run python -m pytest tests/test_upload.py tests/test_pipeline.py -q 2>&1 | tail -3   # Phase 5a+5b baseline green
  ```
- **Rollback:** N/A.

### Step 2 — Extend `apps/documents/exceptions.py`

- **Goal:** add `DocumentNotFoundError` (404).
- **Files touched:** `apps/documents/exceptions.py`.
- **Append (do NOT modify existing classes):**
  ```python
  class DocumentNotFoundError(UploadError):
      """Document with the given doc_id doesn't exist in this tenant/bot."""

      http_status = 404
      code = "document_not_found"
  ```
- **Verification:**
  ```bash
  uv run python -c "
  from apps.documents.exceptions import DocumentNotFoundError, UploadError
  assert issubclass(DocumentNotFoundError, UploadError)
  assert DocumentNotFoundError.http_status == 404
  assert DocumentNotFoundError.code == 'document_not_found'
  e = DocumentNotFoundError('not found', details={'doc_id': 'x'})
  assert e.message == 'not found' and e.details == {'doc_id': 'x'}
  print('exceptions ext OK')
  "
  uv run ruff check apps/documents/exceptions.py
  ```
- **Rollback:** delete the appended class.

### Step 3 — Extend `apps/ingestion/pipeline.py`

- **Goal:** add `DeleteResult` dataclass + `DeletePipeline` class. Imports `DocumentNotFoundError` from the extended exceptions module. Reuses `upload_lock`, `delete_by_doc_id`, `QdrantError`, `Document`.
- **Files touched:** `apps/ingestion/pipeline.py`.
- **Edits (append to the end of the file):**
  1. Add `DocumentNotFoundError` to the existing `from apps.documents.exceptions import (...)` block.
  2. Add `DeleteResult` frozen dataclass (`doc_id: str`, `chunks_deleted: int`, `was_already_deleted: bool`).
  3. Add `DeletePipeline.execute(*, tenant_id, bot_id, doc_id)` per spec body. Critical: cross-tenant guard — after `existing = Document.objects.filter(doc_id=doc_id).first()`, BOTH "no row" AND "row with mismatched tenant_id/bot_id" raise `DocumentNotFoundError` (don't leak existence).
  4. Use Phase 5b's `upload_lock` (already imported); same 5s timeout default.
  5. `Document.save(update_fields=["status", "chunk_count", "error_message", "last_refreshed_at"])` — explicitly include `last_refreshed_at` so `auto_now=True` fires (per Phase 5b's verified pattern).
- **Verification:**
  ```bash
  uv run python manage.py check
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from apps.ingestion.pipeline import DeletePipeline, DeleteResult, UploadPipeline
  assert callable(DeletePipeline.execute)
  print('pipeline ext OK')
  "
  uv run ruff check apps/ingestion/pipeline.py
  ```
- **Rollback:** revert the file.

### Step 4 — Extend `apps/documents/views.py`

- **Goal:** add `DeleteDocumentView(APIView)` with `delete(request, tenant_id, bot_id, doc_id)` handler. Mirrors `UploadDocumentView`'s try/except structure: validate_slug → run pipeline → catch ConcurrentUploadError specially (add Retry-After header) → catch UploadError generically → return 204 on success.
- **Files touched:** `apps/documents/views.py`.
- **Edits:**
  1. Update the `from apps.ingestion.pipeline import ...` to also pull `DeletePipeline`.
  2. Append the `DeleteDocumentView` class.
  3. Wrap the body in the same outer `try/except Exception` as the upload view returning 500 with `{"error":{"code":"internal_error", ...}}` envelope (Phase 5a-shipped pattern).
  4. INFO log on success: `delete_succeeded_response` with `tenant_id, bot_id, doc_id, chunks_deleted, was_already_deleted, status_code=204, elapsed_ms`. ERROR log on failure with `exc_info=True`.
- **Verification:**
  ```bash
  uv run python manage.py check
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from apps.documents.views import DeleteDocumentView, UploadDocumentView
  print('views ext OK')
  "
  uv run ruff check apps/documents/views.py
  ```
- **Rollback:** revert the file.

### Step 5 — Extend `apps/documents/urls.py`

- **Goal:** add the DELETE route alongside the existing POST.
- **Files touched:** `apps/documents/urls.py`.
- **Edits (single import line + single new path entry):**
  1. Update import: `from apps.documents.views import DeleteDocumentView, UploadDocumentView`.
  2. Append `path("tenants/<str:tenant_id>/bots/<str:bot_id>/documents/<uuid:doc_id>", DeleteDocumentView.as_view(), name="delete-document")`.
- **Verification:**
  ```bash
  uv run python manage.py check
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from django.urls import resolve, reverse
  upload_url = reverse('upload-document', kwargs={'tenant_id':'a1b','bot_id':'c2d'})
  delete_url = reverse('delete-document', kwargs={'tenant_id':'a1b','bot_id':'c2d','doc_id':'00000000-0000-0000-0000-000000000000'})
  assert upload_url == '/v1/tenants/a1b/bots/c2d/documents'
  assert delete_url == '/v1/tenants/a1b/bots/c2d/documents/00000000-0000-0000-0000-000000000000'
  match = resolve(delete_url)
  assert match.view_name == 'delete-document'
  print('routing OK; upload=', upload_url, '; delete=', delete_url)
  "
  uv run ruff check apps/documents/urls.py
  ```
- **Rollback:** revert the file.

### Step 6 — Quality gates + Phase 5 regression

- **Goal:** prove the structural changes don't regress Phase 5.
- **Files touched:** none.
- **Commands:**
  ```bash
  uv run python manage.py check
  uv run python manage.py makemigrations --check --dry-run
  uv run ruff check .
  uv run ruff format --check .
  # Phase 5 regression (host-side, with cached embedder)
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
      uv run python -m pytest tests/test_upload.py tests/test_pipeline.py tests/test_locks.py -v 2>&1 | tail -5
  ```
  Expected: 16 (test_upload) + 5 (test_pipeline) + 3 skipped (test_locks SQLite) = 21 passed + 3 skipped.
- **Rollback:** N/A.

### Step 7 — Write `tests/test_delete.py`

- **Goal:** 9 tests per spec. Reuses Phase 5a's `valid_pdf_doc.json` fixture for the upload step.
- **Files touched:** `tests/test_delete.py` (NEW).
- **Tests (verbatim from spec):**
  - `test_204_delete_existing_doc` — POST + DELETE → 204 + empty body.
  - `test_204_idempotent_redelete` — POST + DELETE + DELETE → both 204.
  - `test_404_nonexistent_doc` — DELETE random uuid → 404 + envelope.
  - `test_400_invalid_tenant_slug` — DELETE with `Pizza-Palace` → 400.
  - `test_400_invalid_bot_slug` — DELETE with `Bad-Bot` → 400.
  - `test_404_malformed_uuid_returns_django_404` — DELETE with `not-a-uuid` → 404 (Django default page; assert status only).
  - `test_document_soft_deleted_in_postgres` — POST + DELETE; Document row has `status="deleted"`, `chunk_count=0`.
  - `test_qdrant_chunks_gone_after_delete` — POST + count chunks > 0 + DELETE + count == 0.
  - `test_404_cross_tenant_doc_id` — POST in tenant_a, DELETE same doc_id from tenant_b → 404.
- **Critical detail (matches Phase 5a/5b pattern):** add the same autouse `_bypass_pg_advisory_lock_for_sqlite_tests` fixture from Phase 5a's `tests/test_upload.py` (patches `apps.ingestion.pipeline.upload_lock` to no-op for SQLite host runs). Without this, the DELETE pipeline's lock acquire fails on SQLite.
- **Verification:**
  ```bash
  uv run ruff check tests/test_delete.py
  uv run python -m pytest tests/test_delete.py --collect-only 2>&1 | tail -15
  # Active run (with cached embedder + live Qdrant)
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
      uv run python -m pytest tests/test_delete.py -v
  ```
  Expected: 9/9 green if embedder + Qdrant available; otherwise some tests skip via `qdrant_available` session fixture.
- **Rollback:** delete the file.

### Step 8 — Manual curl smoke (optional, depends on docker access)

- **Goal:** prove end-to-end against the running stack.
- **Status:** docker socket permission may still be blocked (Phase 1+2+3+4+5a+5b outstanding §1). If unblocked: rebuild + smoke. If blocked: defer to host-equivalent pytest path (Step 7 above already covers identical code paths).
- **Commands (when unblocked):**
  ```bash
  make down && make up && sleep 90 && make health
  docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full   # warm embedder

  DOC_ID=$(uuidgen)
  sed "s/^{/{\"doc_id\":\"$DOC_ID\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json
  curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
       -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\n%{http_code}\n"   # 201
  curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\n%{http_code}\n"   # 204
  curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\n%{http_code}\n"   # 204
  curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$(uuidgen) -w "\n%{http_code}\n"   # 404
  curl -sS -X DELETE http://localhost:8080/v1/tenants/Bad-Slug/bots/sup/documents/$(uuidgen) -w "\n%{http_code}\n"   # 400
  ```
- **Rollback:** N/A (verification only).

### Step 9 — Final regression sweep + don't-touch audit + report

- **Goal:** prove the whole repo is still green and only the 4 + 1 spec'd files were touched.
- **Commands:**
  ```bash
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge uv run python -m pytest -v
  curl -fsS http://localhost:8080/healthz | python -m json.tool
  uv run ruff check . && uv run ruff format --check .

  # Mtime audit (no git in repo)
  stat -c '%y %n' \
      apps/core/* \
      apps/tenants/* \
      apps/qdrant_core/* \
      apps/ingestion/{embedder,chunker,payload,locks,apps,__init__}.py \
      apps/documents/{models,admin,serializers,apps,__init__}.py \
      config/* \
      tests/{conftest,test_settings,test_healthz,test_models,test_naming,test_qdrant_client,test_qdrant_collection,test_chunker,test_payload,test_embedder,test_upload,test_pipeline,test_locks,__init__}.py \
      Dockerfile docker-compose.yml docker-compose.override.yml Makefile manage.py pyproject.toml uv.lock scripts/verify_setup.py
  ```
  All listed files must show mtimes ≤ Phase 5b session end (≈ 14:19 today on 2026-04-27).
- **Implementation report** at `build_prompts/phase_6_delete_api/implementation_report.md`.

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Detection |
|---|---|---|---|---|---|
| R1 | Cross-tenant doc_id collision returns 500 instead of 404 | Medium | Information leak (existence implied by 500 vs 404); poor UX | Spec body uses `Document.objects.filter(doc_id=...).first()` + explicit `tenant_id`/`bot_id` match check; both miss-cases raise `DocumentNotFoundError` (404). | Test `test_404_cross_tenant_doc_id` |
| R2 | `<uuid:doc_id>` URL converter test asserts envelope shape | Low | Phase 6 test fails on Django's default 404 page (HTML/plain) | Test asserts `status_code == 404` ONLY, no body assertion | Test `test_404_malformed_uuid_returns_django_404` |
| R3 | Phase 5b's `ConcurrentUploadError` reused on DELETE-side contention | Low | Error code string `"concurrent_upload"` may mislead clients on a delete operation | Spec accepts this for v1; renaming to `ConcurrentOperationError` is Phase 8 polish; clients shouldn't case on the literal string | View test asserts code == `concurrent_upload` |
| R4 | `Document.save(update_fields=[...])` missing `last_refreshed_at` | Medium | `auto_now` doesn't fire; audit timestamp stale | Spec list explicitly includes `last_refreshed_at`. Phase 5b verified this pattern works. | Implicit via `last_refreshed_at` advancing on soft-delete |
| R5 | Idempotent re-delete on a soft-deleted Document | Low | If pipeline tries to call `delete_by_doc_id` against an empty collection, Phase 3's helper returns 0 without raising. The save() call writes the same fields again (no-op for ORM). Returns 204. | Spec is explicit; `was_already_deleted` flag captured | Test `test_204_idempotent_redelete` |
| R6 | DELETE auto-creates tenant/bot rows | Low | Spec says DELETE does NOT auto-create; only POST does. The pipeline does `.filter(...).first()` — no get_or_create | Implicit by code structure (no Tenant.get_or_create in DeletePipeline) | Test `test_404_nonexistent_doc` shouldn't see Tenant created |
| R7 | Pipeline transaction safety: Qdrant succeeds, Postgres fails | Low | Chunks gone but Document.status still "active". On retry, delete_by_doc_id is no-op + Postgres save retries. Idempotent — eventually consistent. | Document the order: Qdrant FIRST, Postgres SECOND, both retried on next call | Phase 8 monitoring |
| R8 | Test fixture pollution on shared (tenant_id, bot_id) | Low | Tests collide if slugs reused | Phase 5a's `fresh_bot` fixture uses `uuid.hex[:8]` per test; cleanup drops collection in teardown | Run `pytest tests/test_delete.py -v` twice — both green |
| R9 | Embedder cold load on `uploaded_doc` fixture's POST | Acceptable | First test in session pays ~30s | Operationally same as Phase 5a; cached BGE-M3 in `~/.cache/bge` keeps subsequent runs fast | Wall time on first test |
| R10 | Phase 1-5 regression from accidental modification | Low | Prior tests fail | Mtime audit + ruff/format gates after edits | Step 9 |
| R11 | Path order in urls.py: more-specific path declared after less-specific path that captures more URL space | Low | URL dispatch routes wrong | Both paths share the prefix `tenants/<>/bots/<>/documents`; the upload path has no trailing segment, the delete path has `/<uuid:doc_id>`. Django matches by exact pattern, not prefix; both route correctly regardless of order. | manage.py check + reverse() in Step 5 |
| R12 | View imports `DeletePipeline` but not `DeleteResult` | Low | Type annotations might fail | View doesn't need `DeleteResult` (it's an internal pipeline return type); only `DeletePipeline` import needed | manage.py check |
| R13 | DRF `Response(status=204)` accidentally has body | Low | Spec says 204 must have no body; `assert response.content == b""` test would fail | Don't pass `data=` to `Response()`. Phase 5b's view pattern is the reference. | Test `test_204_delete_existing_doc` |
| R14 | tests/test_delete.py without autouse pg_advisory_lock bypass | High | DeletePipeline calls upload_lock → SQLite lacks pg_advisory_lock → all delete tests fail with SQL error | Add the same autouse fixture pattern from Phase 5a's test_upload.py | Initial test run would error |

---

## 5. Verification checkpoints

| # | Where | Command | Expected |
|---|---|---|---|
| V1 | After Step 2 (exceptions) | Import smoke | `DocumentNotFoundError` constructible; `http_status=404` |
| V2 | After Step 3 (pipeline) | manage.py check + import smoke | OK |
| V3 | After Step 4 (views) | manage.py check + import smoke | OK |
| V4 | After Step 5 (urls) | manage.py check + `reverse('delete-document', ...)` | URL resolves to `/v1/tenants/<a>/bots/<b>/documents/<uuid>` |
| V5 | After Step 6 (quality gates) | ruff + Phase 5 pytest | Phase 5 tests green |
| V6 | After Step 7 (test_delete.py) | `pytest tests/test_delete.py -v` (host with embedder cached) | 9/9 green |
| V7 | After Step 8 (curl smoke, if unblocked) | 5 curl commands | 201 → 204 → 204 → 404 → 400 |
| V8 | After Step 9 (full regression) | full pytest + healthz + mtime audit | 109+9 = 118 passed + 3 skipped (locks SQLite); healthz JSON |

---

## 6. Spec ambiguities & open questions

1. **`Document.objects.filter(doc_id=doc_id).first()` vs `.get()`.** `.get()` raises `DoesNotExist` which would need a try/except and conversion to `DocumentNotFoundError`. `.first()` returns `None` which we explicitly check. Spec body uses `.first()` for the explicit None-branch. Plan adopts spec's choice.
2. **`response.content == b""` assertion.** DRF's `Response(status=204)` returns empty body. Test `test_204_delete_existing_doc` asserts this directly. Confirmed in Phase 5b's `test_409_retry_after_header` pattern (which checks both body and headers); `test_204` checks the empty-content invariant.
3. **Order of operations on delete failure.** Spec says: Qdrant FIRST, Postgres SECOND. If Postgres update fails after Qdrant succeeds, Qdrant chunks are gone but Document.status still "active". On retry, `delete_by_doc_id` is no-op (returns 0) and Postgres save() retries. Eventually consistent. Document this in §4 R7.
4. **`Retry-After` header value on 409.** `ConcurrentUploadError.retry_after` defaults to 5 (Phase 5b). View pattern `response["Retry-After"] = str(exc.retry_after)` matches Phase 5b's upload-side handler.
5. **`delete_by_doc_id` against a non-existent collection.** Phase 3's helper returns 0 if collection doesn't exist. Phase 6 accepts this as a valid no-op — Document soft-delete still proceeds. No special case needed in the pipeline body.
6. **DELETE auto-creating tenant/bot rows.** Spec says DELETE does NOT auto-create (different from POST). The pipeline never calls `Tenant.objects.get_or_create` or `Bot.objects.get_or_create`. So a DELETE on `/v1/tenants/never_existed/bots/x/documents/<uuid>` returns 404 (Document not found) without creating any rows. The `test_404_nonexistent_doc` test implicitly verifies this — it doesn't check Tenant.objects.count() but the pattern is correct.
7. **`<uuid:doc_id>` accepts uppercase UUIDs?** Django's UUID converter is case-insensitive by default. The view receives a `uuid.UUID` object. `str(doc_id)` produces canonical lowercase hex with hyphens. Database lookup on `UUIDField` is canonical. No edge case in v1.
8. **`tests/test_delete.py` autouse `_bypass_pg_advisory_lock` fixture.** Required for host-side SQLite runs. Phase 5a's `tests/test_upload.py` pattern is the reference. Without it, `DeletePipeline.execute` fails when it tries to acquire the lock against SQLite.

---

## 7. Files deliberately NOT created / NOT modified

- **All Phase 1/2/3/4/5a/5b files except the 4 spec'd Phase 6 extensions** — verified via mtime audit in Step 9.
- **Specifically not touched:**
  - Phase 5a/5b: `apps/documents/serializers.py`, `apps/documents/models.py`, `apps/documents/admin.py`, `apps/documents/migrations/*`, `apps/ingestion/locks.py`, all three Phase 5a fixtures, `tests/test_upload.py`, `tests/test_pipeline.py`, `tests/test_locks.py`, `config/urls.py`, `pyproject.toml`, `uv.lock`.
  - Phase 4: `apps/ingestion/{embedder,chunker,payload}.py`, all Phase 4 tests.
  - Phase 3: `apps/qdrant_core/*`, all Phase 3 tests.
  - Phase 2: `apps/tenants/*`, all Phase 2 tests + migrations.
  - Phase 1: `config/{settings,wsgi,asgi,celery,__init__}.py`, `apps/core/*`, `apps/grpc_service/*`, Dockerfile, docker-compose*.yml, Makefile, manage.py, scripts/, .env.example, README.md.
- **No new fixtures** — Phase 6 reuses `tests/fixtures/valid_pdf_doc.json` from Phase 5a.
- **Out of scope (per spec):**
  - gRPC search service — Phase 7
  - Atomic version swap — v2
  - Audit log table — v3
  - Hard-deleting Document row — v3
  - Bulk delete — v5
  - Async deletion via Celery — v2
  - Renaming `ConcurrentUploadError` → `ConcurrentOperationError` — Phase 8 polish

---

## 8. Acceptance-criteria mapping

| # | Criterion | Build step | Verification | Expected |
|---|---|---|---|---|
| 1 | `uv run ruff check .` zero violations | Steps 2–7 + Step 9 | `uv run ruff check .` | `All checks passed!` |
| 2 | `uv run ruff format --check .` zero changes | Same | `uv run ruff format --check .` | `N files already formatted` |
| 3 | `manage.py check` exits 0 | Steps 3, 4, 5, 6 | `uv run python manage.py check` | `System check identified no issues (0 silenced).` |
| 4 | `makemigrations --check --dry-run` no pending | Step 6 | `uv run python manage.py makemigrations --check --dry-run` | `No changes detected` |
| 5 | `make up && sleep 90 && make health` green | Step 8 (when docker unblocked) | `make health` | green JSON |
| 6 | curl 201 → 204 → 204 → 404 → 400 sequence | Step 8 | 5 curl commands | matches spec sequence |
| 7 | Container `pytest tests/test_delete.py -v` green | Step 7 (canonical) or Step 8 | docker compose exec or host-equivalent | 9/9 green |
| 8 | `uv run pytest -v` keeps prior phase tests green | Step 9 | full host pytest | 109 + 9 = 118 passed (or skipped where applicable) |
| 9 | `git status --short` shows ONLY Phase 6 files | Step 9 | mtime audit (no git) | 4 modified + 1 new test + report |
| 10 | Phase 1-5 regression + healthz | Step 9 | suite + curl | all green |

If docker socket permission still blocked, criteria 5/6/7 satisfy via host-equivalent: `pytest tests/test_delete.py` covers identical code paths against live Qdrant + cached BGE-M3.

---

## 9. Tooling commands cheat-sheet

```bash
# Per-step verification
uv run python -c "..."                            # smoke imports
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run ruff check . && uv run ruff format --check .

# URL routing
uv run python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from django.urls import reverse
print(reverse('delete-document', kwargs={'tenant_id':'a1b','bot_id':'c2d','doc_id':'00000000-0000-0000-0000-000000000000'}))
"

# Phase 6 tests (host-equivalent)
QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
    uv run python -m pytest tests/test_delete.py -v

# Full regression
QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge uv run python -m pytest -v

# Inside container (canonical, when docker unblocked)
docker compose -f docker-compose.yml exec web pytest tests/test_delete.py -v
docker compose -f docker-compose.yml exec web pytest -v

# Manual curl smoke (lifecycle)
make up && sleep 90 && make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
DOC_ID=$(uuidgen)
sed "s/^{/{\"doc_id\":\"$DOC_ID\",/" tests/fixtures/valid_pdf_doc.json > /tmp/with-id.json
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" -d @/tmp/with-id.json -w "\nHTTP %{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\nHTTP %{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$DOC_ID -w "\nHTTP %{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/test_t/bots/test_b/documents/$(uuidgen) -w "\nHTTP %{http_code}\n"
curl -sS -X DELETE http://localhost:8080/v1/tenants/Bad-Slug/bots/sup/documents/$(uuidgen) -w "\nHTTP %{http_code}\n"

# Health regression
curl -fsS http://localhost:8080/healthz | python -m json.tool
```

---

## 10. Estimated effort

| Step | Task | Effort |
|---|---|---|
| 1 | Read & inventory | 5 min |
| 2 | exceptions.py extension | 5 min |
| 3 | pipeline.py extension | 15 min |
| 4 | views.py extension | 15 min |
| 5 | urls.py extension | 5 min |
| 6 | Quality gates + Phase 5 regression | 5 min |
| 7 | test_delete.py | 25 min |
| 8 | Manual curl smoke (deferred if docker blocked) | 5 min |
| 9 | Final regression + audit + report | 15 min |
| | **Total** | **~1.5 hours** wall clock — shortest phase since Phase 1 |

---

## End of plan

Once Phase 6 ships green, **the document lifecycle (upload + delete) is complete.** Phase 7 (gRPC search service) is unblocked.
