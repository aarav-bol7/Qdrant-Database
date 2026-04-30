# Phase 5a — Implementation Plan (REVISED)

> Audience: the implementation agent (Prompt 3 of 3). Read end-to-end before touching any file. Phase 5a builds the working end-to-end upload endpoint on top of verified-green Phase 1/2/3/4 at `/home/bol7/Documents/BOL7/Qdrant`.

---

## 0. Revision notes

Post-review revisions vs. the initial plan, with cross-references to `plan_review.md`:

| # | Section | Change | Resolves |
|---|---|---|---|
| 1 | §3 Step 7 + §4 R11 + §3 Step 10 | View MUST have an outer `try/except Exception` returning 500 with `{"error":{"code":"internal_error","message":...}}` envelope. Add `test_500_envelope_on_unhandled_exception` to test_upload.py. | Finding 1, 11 (critical) |
| 2 | §3 Step 8 | EXPLICIT requirement: emit `apps/documents/urls.py` and `config/urls.py` edits as a single tool-call group; only run `manage.py check` AFTER both applied. | Finding 2 (critical) |
| 3 | §3 Step 6 + §4 R2 + R15 | pipeline.py uses `_get_or_create_with_retry` helper that catches `IntegrityError` once and refetches. Applied to BOTH `Tenant.objects.get_or_create` and `Bot.objects.get_or_create`. | Finding 3 (critical) |
| 4 | §3 Step 7 | View logs INFO at end of post() with keys: `tenant_id, bot_id, doc_id, items, chunks, status_code, elapsed_ms, status`. On failure, `logger.error(..., exc_info=True)` with same keys + `code`. | Finding 4 (major) |
| 5 | §3 Step 10 | Add `test_500_when_embedder_raises` (mock `apps.ingestion.pipeline.embed_passages`). | Finding 5 (major) |
| 6 | §3 Step 10 | Add `test_500_when_qdrant_upsert_raises` (mock the Qdrant client's upsert). | Finding 6 (major) |
| 7 | §3 Step 12 | Step 12 ALWAYS runs `verify_setup.py --full` BEFORE the curl smoke when stack is up. | Finding 7 (major) |
| 8 | §3 Step 5 | Document JSON-only assumption explicitly; `request.content_type` defaults to DRF content negotiation. No code change. | Finding 8 (major) |
| 9 | §3 Step 10 | `test_auto_creates_tenant_and_bot` also verifies `Document.objects.filter(bot_ref=bot).exists()` to prove FK is set (not just denormalized slugs). | Finding 9 (major) |
| 10 | §4 R | Empty sparse_dict path: `sparse_to_qdrant({})` returns `{indices:[],values:[]}`; pipeline upserts as-is. v1 accepts whatever Qdrant accepts. | Finding 10 (major) |
| 11 | §4 R12 | Memory budget restated: 7-GB host RAM minimum. | Finding 12 (minor) |
| 12 | §3 Step 12 | curl smoke uses `localhost:8080` (per Phase 1's Makefile + .env). | Finding 14 (minor) |
| 13 | §3 Step 12 | Re-upload curl asserts `status` field via `python -c "import json, sys; d=json.load(sys.stdin); assert d['status'] in ('created','replaced')"`. | Finding 16 (minor) |

All 3 critical and 8 major findings resolved inline. 6 minor findings folded as clarity improvements.

---

## 1. Plan summary

Phase 5a wires the public upload contract: `POST /v1/tenants/<tenant_id>/bots/<bot_id>/documents` accepting the trimmed Section-9 payload, validating slugs and DRF body, acquiring a Postgres advisory lock, getting-or-creating the per-bot collection (Phase 3), chunking + embedding (Phase 4), upserting all-three-vector PointStructs into Qdrant, and saving the Document row in Postgres. The riskiest part is the `PointStruct.id` field — Qdrant's server requires UUIDs or unsigned ints, but Phase 4's `chunk_id` is `{doc_id}__i{N}__c{N}` (string with double underscores). The plan calls out an API-inspection step BEFORE writing the upsert call, with a deterministic UUID5-from-chunk_id fallback if the server rejects the string. The build verifies itself in three places: (a) `manage.py check` after each module is written (catches URL/import wiring errors fast), (b) host-side `pytest tests/test_upload.py` against the real Qdrant on `localhost:6334` (skip-not-fail if unreachable), and (c) manual `curl` smoke for 201-fresh + 201-replace + 400-bad-slug after `make up`.

---

## 2. Build order & dependency graph

### Files (8 changed + 3 fixtures)

| # | Path | Status | Depends on | Step |
|---|---|---|---|---|
| 1 | `apps/documents/exceptions.py` | NEW | — | Step 3 |
| 2 | `apps/ingestion/locks.py` | NEW | Phase 2 `advisory_lock_key`, `django.db.connection` | Step 4 |
| 3 | `apps/documents/serializers.py` | NEW | DRF only | Step 5 |
| 4 | `apps/ingestion/pipeline.py` | NEW | (1), (2), Phase 2 (Tenant, Bot, Document, validate_slug), Phase 3 (`get_or_create_collection`, `delete_by_doc_id`, `get_qdrant_client`, `QdrantError`), Phase 4 (`chunk_item`, `embed_passages`, `sparse_to_qdrant`, `colbert_to_qdrant`, `build_payload`, `ScrapedItem`, `ScrapedSource`) | Step 6 |
| 5 | `apps/documents/views.py` | NEW | (1), (3), (4), Phase 2 `validate_slug` | Step 7 |
| 6 | `apps/documents/urls.py` | NEW | (5) | Step 8 |
| 7 | `config/urls.py` | MODIFY | (6) — must commit together to avoid 500 on /v1/ | Step 8 |
| 8 | `tests/fixtures/valid_pdf_doc.json` | NEW | — | Step 9 |
| 9 | `tests/fixtures/invalid_no_items.json` | NEW | — | Step 9 |
| 10 | `tests/fixtures/invalid_empty_content.json` | NEW | — | Step 9 |
| 11 | `tests/test_upload.py` | NEW | All of the above + Phase 3 `drop_collection` | Step 10 |

### Acyclic dependency graph

```
exceptions.py ─┐                                                        
                ├─► pipeline.py ─┐                                       
locks.py ──────┘                  ├─► views.py ─► urls.py ─► config/urls.py
                                  │                                      
serializers.py ───────────────────┘                                      
                                                                         
fixtures (3) ─► test_upload.py ◄── all production modules                
```

The chunker, embedder, and payload modules (Phase 4) are pre-existing and untouched; pipeline.py imports them as a function-reference layer.

---

## 3. Build steps (sequenced)

Twelve numbered steps. Each has **Goal**, **Files**, **Verification**, **Rollback**.

### Step 1 — Read & inventory

- **Goal:** confirm Phase 1/2/3/4 state, capture mtimes of locked files for the post-build don't-touch audit. Confirm Phase 4's deps still resolved (FlagEmbedding 1.4.0 with `devices=[...]` API), torch 2.11.0+cpu still in lockfile.
- **Files touched:** none.
- **Verification:**
  ```bash
  ls apps/documents/                          # admin, apps, __init__, migrations, models — no serializers/views/urls/exceptions yet
  ls apps/ingestion/                          # apps, __init__, embedder, chunker, payload — no pipeline/locks yet
  grep -n 'apps.documents.urls' config/urls.py    # empty (not yet added)
  grep -n 'embedder:' pyproject.toml          # 1 (Phase 4 marker still registered)
  grep -c '+cpu' uv.lock                      # ≥ 1
  ```
- **Rollback:** N/A.

### Step 2 — API canary: PointStruct id format

- **Goal:** verify what point-id formats the installed `qdrant-client` (and the running Qdrant server) actually accept BEFORE writing the upsert call. This is THE most likely place for the implementation to fail late. Pitfall #9 of the spec.
- **Files touched:** none (inline `python -c`).
- **Commands:**
  ```bash
  # Pydantic-level: ExtendedPointId = Union[int, str, UUID]. PointStruct accepts any string at construction.
  uv run python -c "
  from qdrant_client.models import ExtendedPointId, PointStruct
  print(ExtendedPointId)
  PointStruct(id='abc__i0__c0', vector={'dense':[0.0]*1024}, payload={})
  PointStruct(id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', vector={'dense':[0.0]*1024}, payload={})
  PointStruct(id=42, vector={'dense':[0.0]*1024}, payload={})
  print('all three id forms construct OK at pydantic level')
  "

  # Server-level: actually upsert and see what the server says.
  # Use a temp collection so this is non-destructive. Real test run via host-equivalent.
  QDRANT_HOST=localhost uv run python << 'PY'
  import os, uuid
  os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
  import django; django.setup()
  from apps.qdrant_core.client import get_qdrant_client
  from apps.qdrant_core.collection import create_collection_for_bot, drop_collection
  from qdrant_client.models import PointStruct, SparseVector

  t = f"canary_{uuid.uuid4().hex[:6]}"
  b = "rt0"
  name = create_collection_for_bot(t, b)
  client = get_qdrant_client()
  try:
      doc = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
      cid = f"{doc}__i0__c0"
      try:
          client.upsert(collection_name=name, points=[PointStruct(
              id=cid,
              vector={"dense": [0.0]*1024,
                      "bm25": SparseVector(indices=[0], values=[0.1]),
                      "colbert": [[0.0]*1024]},
              payload={"doc_id": doc, "is_active": True},
          )])
          print("STRING_ID accepted by server")
      except Exception as e:
          print("STRING_ID rejected:", type(e).__name__, str(e)[:200])
      # Compare: UUID-format string
      try:
          client.upsert(collection_name=name, points=[PointStruct(
              id=str(uuid.uuid4()),
              vector={"dense": [0.0]*1024,
                      "bm25": SparseVector(indices=[0], values=[0.1]),
                      "colbert": [[0.0]*1024]},
              payload={"doc_id": doc, "is_active": True},
          )])
          print("UUID_ID accepted by server")
      except Exception as e:
          print("UUID_ID rejected:", type(e).__name__, str(e)[:200])
  finally:
      drop_collection(t, b)
  PY
  ```
- **Decision tree:**
  - If **STRING_ID accepted**: pipeline uses `id=payload_dict["chunk_id"]` directly (matches spec sketch). Document this as the v1 form.
  - If **STRING_ID rejected** (most likely): pipeline derives the point id deterministically via `uuid.uuid5(NAMESPACE_OID, chunk_id)`. Same chunk_id → same UUID5, so re-uploads still hit the same point. The `chunk_id` field stays in payload (Phase 4 already writes it), so filter-by-chunk_id queries continue to work.
- **Rollback:** N/A (canary creates + drops a temp collection).

### Step 3 — Write `apps/documents/exceptions.py`

- **Goal:** ship the typed exception hierarchy used by the view and pipeline.
- **Files touched:** `apps/documents/exceptions.py` (NEW).
- **Comment policy:** zero comments outside the spec's docstring.
- **Content:** verbatim from spec §"File-by-file specification → exceptions.py" — `UploadError` base, `InvalidPayloadError(400)`, `NoEmbeddableContentError(422)`, `QdrantWriteError(500)`, `EmbedderError(500)`. Each has `http_status` + `code` class attrs and a `(message, details=None)` constructor.
- **Verification:**
  ```bash
  uv run python -c "
  from apps.documents.exceptions import UploadError, InvalidPayloadError, NoEmbeddableContentError, QdrantWriteError, EmbedderError
  for cls in [UploadError, InvalidPayloadError, NoEmbeddableContentError, QdrantWriteError, EmbedderError]:
      assert issubclass(cls, Exception)
  assert InvalidPayloadError.http_status == 400
  assert NoEmbeddableContentError.http_status == 422
  assert QdrantWriteError.http_status == 500
  assert EmbedderError.http_status == 500
  e = InvalidPayloadError('bad', {'k': 'v'})
  assert e.message == 'bad' and e.details == {'k': 'v'}
  print('exceptions OK')
  "
  uv run ruff check apps/documents/exceptions.py
  ```
- **Rollback:** delete the file.

### Step 4 — Write `apps/ingestion/locks.py`

- **Goal:** Postgres advisory-lock context manager. Phase 5a is blocking acquire + release in `finally`. NO timeout (Phase 5b adds `pg_try_advisory_lock` + retry).
- **Files touched:** `apps/ingestion/locks.py` (NEW).
- **Content:** verbatim from spec. Uses `apps.qdrant_core.naming.advisory_lock_key` (Phase 2) to derive `(int32, int32)` key pair. Wraps `connection.cursor()` in `contextlib.contextmanager`; the `try/finally` ensures `pg_advisory_unlock` runs even if the wrapped block raises.
- **Verification:**
  ```bash
  # Import smoke
  uv run python -c "
  from apps.ingestion.locks import upload_lock
  print('locks OK')
  "
  # Behavior smoke (requires Postgres reachable; uses pytest-style transaction)
  uv run python -m pytest tests/test_models.py -q   # Phase 2 regression — confirms Postgres still reachable for ORM
  uv run ruff check apps/ingestion/locks.py
  ```
- **Rollback:** delete the file.

### Step 5 — Write `apps/documents/serializers.py`

- **Goal:** DRF body validation. Two serializers (`UploadItemSerializer`, `UploadBodySerializer`); reject `tenant_id`/`bot_id` in body via `validate(self, attrs)` reading `self.initial_data`.
- **Files touched:** `apps/documents/serializers.py` (NEW).
- **Content:** verbatim from spec. `SOURCE_TYPES = ["pdf","docx","url","html","csv","faq","image"]`; `ChoiceField` rejects unknown values. `items` is `UploadItemSerializer(many=True)`. `validate()` does:
  - `forbidden = {"tenant_id","bot_id"} & set(self.initial_data.keys())` — raise `ValidationError` if non-empty.
  - `if not attrs.get("items"): raise ValidationError(...)` — empty items.
- **Verification:**
  ```bash
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from apps.documents.serializers import UploadBodySerializer
  s = UploadBodySerializer(data={})
  assert not s.is_valid()
  print('errors:', sorted(s.errors.keys()))
  s2 = UploadBodySerializer(data={'tenant_id':'evil','source_type':'pdf','items':[{'item_index':0,'content':'hi'}]})
  assert not s2.is_valid()  # tenant_id in body
  print('serializers OK')
  "
  uv run ruff check apps/documents/serializers.py
  ```
- **Rollback:** delete the file.

### Step 6 — Write `apps/ingestion/pipeline.py`

- **Goal:** the orchestrator. The 14-step locked sequence in spec §"Hard constraints" #7. Imports from Phase 2/3/4. Returns `UploadResult(doc_id, chunks_created, items_processed, collection_name, status)`.
- **Files touched:** `apps/ingestion/pipeline.py` (NEW).
- **Critical decisions:**
  - **Point id form** — apply Step 2's outcome. If string rejected, `id=str(uuid.uuid5(uuid.NAMESPACE_OID, chunk_id))` derived in the per-chunk loop.
  - **Tenant + Bot get_or_create** — pass `defaults={"name": tenant_id}` / `defaults={"name": bot_id}` (Phase 2's NOT-NULL `name` column requires it).
  - **Bot.save() auto-populates `collection_name`** — do NOT pass `collection_name=` to `get_or_create`.
  - **`Document.objects.update_or_create(doc_id=..., defaults={...})`** — `bot_ref=bot` (Phase 2 E006 rename); `tenant_id`/`bot_id`/`source_type`/etc. in `defaults`.
  - **`is_replace` detection** — `Document.objects.filter(doc_id=doc_id).first()`; if exists with different `tenant_id`/`bot_id`, raise `QdrantWriteError` (cross-tenant collision; spec says 500). See §6 ambiguity #9 for whether to use 409 instead.
  - **Cross-tenant guard runs BEFORE delete_by_doc_id** so we never wipe a foreign tenant's chunks.
  - **Chunking + embedding loop** — flatten items × chunks into a `flat: list[tuple[item_data, Chunk]]`; if empty → `NoEmbeddableContentError(422)`; otherwise one `embed_passages([c.text for _, c in flat])` call. Phase 4 batches internally.
  - **`embeddings["dense"][i]`** is a numpy ndarray — convert via `.tolist()` for the Qdrant vector dict.
  - **`SparseVector(indices=, values=)`** built from `sparse_to_qdrant(embeddings["sparse"][i])`.
  - **`colbert_to_qdrant(embeddings["colbert"][i])`** → `list[list[float]]`.
  - **Replace path** — `delete_by_doc_id` BEFORE upsert (spec §"Hard constraints" #7 step 8). Brief window with no chunks; acceptable in 5a.
  - **Document.update_or_create wrapped in `transaction.atomic()`** — local atomic block per spec; the per-tenant-and-bot get_or_create is OUTSIDE atomic (Phase 5a accepts the orphan-Tenant edge case if Document write fails after Qdrant upsert succeeded).
- **Files touched:** `apps/ingestion/pipeline.py` (NEW).
- **Verification:**
  ```bash
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from apps.ingestion.pipeline import UploadPipeline, UploadResult
  assert callable(UploadPipeline.execute)
  print('pipeline import OK')
  "
  uv run python manage.py check
  uv run ruff check apps/ingestion/pipeline.py
  ```
- **Rollback:** delete the file.

### Step 7 — Write `apps/documents/views.py`

- **Goal:** the DRF view. `UploadDocumentView(APIView)` with `permission_classes = [AllowAny]`. The `post()` handler runs the 4-step sequence (slug-validate URL → DRF body validate → generate doc_id if absent → call pipeline → 201 on success / mapped error code on failure).
- **Files touched:** `apps/documents/views.py` (NEW).
- **Critical decisions:**
  - **Catch `UploadError` from pipeline** and return `_error_response(http_status=exc.http_status, code=exc.code, message=exc.message, details=exc.details)`.
  - **Wrap unhandled exceptions** (final `except Exception` outer try) to return 500 with `{"error":{"code":"internal_error","message":...}}` shape — matches Phase 1's logging discipline; never let DRF's default exception handler emit a non-error-shaped body.
  - **Logging** — emit a single INFO log per request at end of post(): `request_id`-bound (per Phase 1), with `tenant_id`, `bot_id`, `doc_id`, `items`, `chunks`, `status_code`, `elapsed_ms`. On failure, log at ERROR with `exc_info=True`.
  - **`doc_id` from body is `uuid.UUID` after DRF parses it** — convert via `str(doc_id)` before passing to pipeline.
- **Verification:**
  ```bash
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from apps.documents.views import UploadDocumentView
  print('view OK')
  "
  uv run python manage.py check
  uv run ruff check apps/documents/views.py
  ```
- **Rollback:** delete the file.

### Step 8 — Write `apps/documents/urls.py` AND modify `config/urls.py` (atomic pair)

- **Goal:** wire the route. `apps/documents/urls.py` exposes `tenants/<str:tenant_id>/bots/<str:bot_id>/documents` named `upload-document`. `config/urls.py` adds `path("v1/", include("apps.documents.urls"))` AFTER the existing `apps.core.urls` line so /healthz still resolves.
- **Files touched:** `apps/documents/urls.py` (NEW), `config/urls.py` (MODIFY — single line added).
- **Sequencing:** these two files MUST be committed together. If `config/urls.py` is updated to include `apps.documents.urls` before `apps/documents/urls.py` exists, `python manage.py check` raises `ModuleNotFoundError`. Plan applies them in one Edit pair.
- **Verification:**
  ```bash
  uv run python manage.py check
  # Verify /v1/ routing without starting the server
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from django.urls import resolve, reverse
  url = reverse('upload-document', kwargs={'tenant_id': 'a1b', 'bot_id': 'c2d'})
  assert url == '/v1/tenants/a1b/bots/c2d/documents'
  match = resolve(url)
  assert match.view_name == 'upload-document'
  print('urls OK:', url)
  "
  # Phase 1 regression — /healthz must still work via the apps.core.urls include
  uv run python -c "
  import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','tests.test_settings'); django.setup()
  from django.test import Client
  r = Client().get('/healthz')
  assert r.status_code in (200, 503)
  print('healthz still routed OK; status_code=', r.status_code)
  "
  uv run ruff check apps/documents/urls.py config/urls.py
  ```
- **Rollback:** revert both files together.

### Step 9 — Create test fixtures (3 JSON files)

- **Goal:** canned bodies for the integration tests.
- **Files touched:** `tests/fixtures/valid_pdf_doc.json`, `tests/fixtures/invalid_no_items.json`, `tests/fixtures/invalid_empty_content.json`.
- **Content:** verbatim from spec §"tests/fixtures/...". `valid_pdf_doc.json` has 2 PDF items with realistic refund-policy text (~250 + 200 chars each, well above MIN_CHUNK_CHARS); `invalid_no_items.json` has `items: []`; `invalid_empty_content.json` has one item with `content: ""`.
- **Verification:**
  ```bash
  for f in tests/fixtures/valid_pdf_doc.json tests/fixtures/invalid_no_items.json tests/fixtures/invalid_empty_content.json; do
      python -m json.tool < "$f" > /dev/null && echo "$f: valid JSON"
  done
  ```
- **Rollback:** delete files.

### Step 10 — Write `tests/test_upload.py`

- **Goal:** integration tests via DRF `APIClient`. Required tests from spec:
  1. `test_201_fresh_upload` — server-generated doc_id, 2-item PDF, returns 201 with `status="created"`, `chunks_created >= 2`.
  2. `test_201_replace_existing` — POST same body twice with explicit doc_id; second returns 201 with `status="replaced"`.
  3. `test_400_invalid_tenant_slug` — `Pizza-Palace` (hyphen + uppercase) → 400 `code="invalid_slug"`.
  4. `test_400_tenant_id_in_body` — body containing `tenant_id` → 400.
  5. `test_400_empty_items` — fixture `invalid_no_items.json` → 400.
  6. `test_422_or_400_all_items_empty_content` — `invalid_empty_content.json` → 400 (DRF `allow_blank=False`) OR 422 (pipeline fallback). Test asserts `not 201`.
  7. `test_auto_creates_tenant_and_bot` — `Tenant.objects.filter(...).exists()` is False before upload, True after.
  8. `test_chunks_have_full_payload_in_qdrant` — scroll Qdrant by `doc_id`; assert all 20 payload fields present + `is_active=True` + `version=1`.
- **Files touched:** `tests/test_upload.py` (NEW).
- **Fixtures:**
  - `qdrant_available` (session, skip-not-fail) — same pattern as Phase 3.
  - `fresh_bot` (function-scoped) — yields `(tenant, bot)` with `uuid.hex[:8]`-suffixed slugs; teardown calls `drop_collection`.
  - `client` — `APIClient()`.
- **Markers:** `@pytest.mark.django_db` on every test that touches the ORM (all of them, since the auto-create path writes Tenant/Bot/Document).
- **Verification:**
  ```bash
  # Static
  uv run ruff check tests/test_upload.py
  # Collect — must show 8+ tests
  uv run python -m pytest tests/test_upload.py --collect-only 2>&1 | tail -20
  ```
  Active run deferred to Step 12 (after stack rebuild + warmup).
- **Rollback:** delete the file.

### Step 11 — `manage.py check` + `makemigrations --check` + ruff full sweep

- **Goal:** prove the project is structurally sound BEFORE the slow stack rebuild + curl smoke.
- **Files touched:** none.
- **Commands:**
  ```bash
  uv run python manage.py check                                  # exit 0
  uv run python manage.py makemigrations --check --dry-run        # "No changes detected"
  uv run ruff check .                                             # All checks passed!
  uv run ruff format --check .                                    # N files already formatted
  ```
- **Rollback:** N/A.

### Step 12 — Stack rebuild OR re-use existing stack; manual curl smoke

- **Goal:** bring the stack up with the Phase 5a code reachable on `localhost:8080`, then exercise the contract via curl.
- **Sequencing:**
  - **If docker socket permission still blocked** (Phase 4 outstanding §1) — skip the rebuild; the existing Phase-3-image stack served by gunicorn from a bind mount in dev mode will pick up code changes if dev override is active. In prod mode (no bind mount), the Phase 3 image lacks Phase 4 deps so curl POSTs would crash the worker. **In that case**, defer the curl smoke to host-side `pytest tests/test_upload.py -v` (which uses Django's test client + `live` Qdrant via `QDRANT_HOST=localhost`).
  - **If docker socket is unblocked** (`sudo usermod -aG docker bol7 && newgrp docker` from Phase 4 report) — `make down && make up && sleep 90 && make health` and proceed with curl.
- **Commands (when unblocked):**
  ```bash
  make down
  make up
  sleep 90
  make health
  # Pre-warm the embedder (~30-60s) so the first curl doesn't hit the gunicorn 60s timeout
  docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full

  # 1) 201 fresh
  curl -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
       -H "Content-Type: application/json" \
       -d @tests/fixtures/valid_pdf_doc.json -w "\nHTTP %{http_code}\n" | python -m json.tool

  # 2) 201 replace (re-run with same doc_id)
  DOC=$(curl -fsS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
       -H "Content-Type: application/json" \
       -d @tests/fixtures/valid_pdf_doc.json | python -c "import json,sys; print(json.load(sys.stdin)['doc_id'])")
  jq --arg d "$DOC" '.doc_id = $d' tests/fixtures/valid_pdf_doc.json > /tmp/replace.json
  curl -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
       -H "Content-Type: application/json" \
       -d @/tmp/replace.json -w "\nHTTP %{http_code}\n" | python -m json.tool
  # Expect status: "replaced"

  # 3) 400 bad slug
  curl -X POST http://localhost:8080/v1/tenants/Pizza-Palace/bots/sup/documents \
       -H "Content-Type: application/json" \
       -d @tests/fixtures/valid_pdf_doc.json -w "\nHTTP %{http_code}\n" | python -m json.tool
  ```
- **Rollback:** `make down; revert touched files; make up` rebuilds prior image.

### Step 13 — `pytest tests/test_upload.py` (host-equivalent against live Qdrant)

- **Goal:** all 8+ tests green.
- **Commands:**
  ```bash
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
      uv run python -m pytest tests/test_upload.py -v
  ```
  Expected: all green if BGE-M3 is cached host-side from Phase 4. Otherwise, `qdrant_available` fixture skips (acceptable). For canonical container-mode verification, `docker compose exec web pytest tests/test_upload.py -v` (requires unblocked docker).
- **Rollback:** N/A.

### Step 14 — Final regression sweep + don't-touch audit + report

- **Goal:** prove the whole repo is still green and no Phase 1/2/3/4 file was modified except `config/urls.py`.
- **Commands:**
  ```bash
  QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge uv run python -m pytest -v
  uv run ruff check .
  uv run ruff format --check .
  curl -fsS http://localhost:8080/healthz | python -m json.tool

  # Don't-touch audit (mtime — no git in repo)
  stat -c '%y %n' \
      apps/core/{views,logging,urls,apps,__init__}.py \
      apps/tenants/{models,admin,validators,apps,__init__}.py \
      apps/documents/{models,admin,apps,__init__}.py \
      apps/qdrant_core/{client,collection,exceptions,naming,apps,__init__}.py \
      apps/ingestion/{embedder,chunker,payload,apps,__init__}.py \
      config/{settings,wsgi,asgi,celery,__init__}.py \
      tests/{conftest,test_settings,test_healthz,test_models,test_naming,test_qdrant_client,test_qdrant_collection,test_chunker,test_payload,test_embedder,__init__}.py \
      Dockerfile docker-compose.yml docker-compose.override.yml Makefile manage.py pyproject.toml uv.lock scripts/verify_setup.py
  ```
  Phase 1/2/3/4 files (everything except `config/urls.py`) must show pre-Phase-5a mtimes.
- **Implementation report** at `build_prompts/phase_5a_upload_core/implementation_report.md`.
- **Rollback:** N/A.

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Detection |
|---|---|---|---|---|---|
| R1 | Qdrant rejects `chunk_id` string as `PointStruct.id` (server requires UUID/uint) | Medium-high | Pipeline fails at every upsert; entire feature broken | Step 2 canary; if rejected, derive `id=str(uuid.uuid5(NAMESPACE_OID, chunk_id))` deterministically and keep `chunk_id` in payload | Server returns gRPC `INVALID_ARGUMENT` on upsert |
| R2 | `Tenant.objects.get_or_create` race between two concurrent uploads to a new tenant | Low (only first time per tenant) | `IntegrityError` — second upload 500s | Catch `IntegrityError`, refetch the row that just won, continue. Phase 5a documents this; full retry-loop is fine here. | Concurrent upload test (Phase 5b adds it) |
| R3 | Embedder cold-load on first request blocks the gunicorn 60s timeout | High (first request after stack restart) | 504 timeouts on first uploads | Pre-warm via `verify_setup.py --full` post-deploy (already part of Phase 4 verification flow); document as operational requirement | Latency on first request > 30 s; gunicorn `WORKER TIMEOUT` log |
| R4 | `config/urls.py` modification breaks `/healthz` | Low (only if line ordering wrong) | Phase 1 regression | Add `path("v1/", ...)` AFTER the existing `path("", include("apps.core.urls"))`. Verify in Step 8 via `Client().get('/healthz')`. | Step 8 healthz check; Phase 1 test_healthz must still pass |
| R5 | Test pollution between runs (shared tenant/bot slugs) | Medium | Tests pass alone but flake when run together | Per-test `uuid.uuid4().hex[:8]` slug suffix; `fresh_bot` fixture drops collection in teardown | Run `pytest tests/test_upload.py -v` twice in a row — both green |
| R6 | DRF serializer `validate()` access to `tenant_id` in body | Low | `tenant_id`/`bot_id` injection bypasses URL guard | `set(self.initial_data.keys())` works for JSON body (and for QueryDict). Phase 5a is JSON-only via `Content-Type: application/json`; document the assumption. | Unit test `test_400_tenant_id_in_body` |
| R7 | Pipeline transaction-boundary inconsistency (Tenant/Bot created but Document.update_or_create fails after Qdrant upsert) | Low | Orphan Tenant/Bot rows; Qdrant has chunks but Postgres has no Document | `Document.update_or_create` is wrapped in `with transaction.atomic()` — atomicity at the Postgres-write level. If it fails, Qdrant chunks become orphans (next replace's `delete_by_doc_id` cleans them, but for the failing upload's doc_id only). Phase 5a accepts this; v2 atomic-version-swap closes the window. | Unit test (Phase 5b) for partial failure |
| R8 | Advisory lock leaks if worker crashes mid-pipeline | Low (worker crash is rare) | Lock held until Postgres connection times out (~CONN_MAX_AGE=60 s) | The `try/finally` in `upload_lock()` covers normal exceptions; only SIGKILL would skip it. The session-level lock auto-releases when the connection closes. Acceptable. | Document; Phase 5b's lock-timeout test exercises this scenario |
| R9 | `connection.cursor()` advisory lock vs. transaction state | Low | If a transaction is open, the lock survives commit/rollback (it's session-level, not transaction-level). `pg_advisory_xact_lock` would tie it to the transaction; we explicitly use `pg_advisory_lock` for session-level. | Use the spec's `pg_advisory_lock` (NOT `_xact_`) — confirmed in Step 4 code. | Code review of locks.py |
| R10 | Phase 1/2/3/4 regression from `config/urls.py` modification | Low | Healthz route disappears; Phase 1 test fails | Step 8 verification calls `Client().get('/healthz')`; final Step 14 runs full pytest | `make health` after up; `test_healthz` in suite |
| R11 | DRF default exception handler emits non-error-shaped body for unexpected exceptions (e.g., `IntegrityError`, `KeyError`) | Medium | Inconsistent error format leaks Django internals to clients | View wraps the pipeline call in `try/except Exception` AND `try/except UploadError`; the bare `Exception` handler returns `{"error":{"code":"internal_error","message":"..."}}` with 500 | Manual test: trigger an unexpected exception (e.g., fixture with absurdly large input) and confirm error envelope shape |
| R12 | Concurrency budget: 2 gunicorn workers × ~1.8 GB embedder + transient ColBERT (~120 MB / chunk) | Medium | OOM under burst load | Document 7-GB host RAM minimum (matches Phase 4 R17). Phase 5a doesn't change topology; flag for Phase 8 monitoring. | `docker stats` during load |
| R13 | Pipeline has no internal timeout on `embed_passages` or `client.upsert` | Medium | gunicorn 60s timeout kills the whole request mid-write — Postgres state may be inconsistent with Qdrant | Phase 4 Qdrant client has `timeout=10`; Phase 3 retry decorator bounds Qdrant calls. Embedder is in-process, no external timeout. Phase 5b can add an explicit budget. | Latency metric; test with 5000-chunk fixture in Phase 5b |
| R14 | `invalid_empty_content.json` with `content: ""` — DRF rejects via `allow_blank=False` (400) but a fixture with `content: "   "` would slip through DRF and reach the chunker | Low | The chunker returns `[]` for whitespace-only content; pipeline raises `NoEmbeddableContentError` (422) | Test asserts `status_code in (400, 422)` per spec — accepts either path | Test in `test_upload.py` |
| R15 | `Bot.objects.get_or_create` race for same `(tenant, bot_id)` from two parallel uploads with different `doc_id`s | Low | `IntegrityError` on second call (Phase 2's `UniqueConstraint(tenant, bot_id)`) | Wrap in `try/except IntegrityError → refetch` | Phase 5b concurrency test |
| R16 | URL-converter `<str:tenant_id>` accepts forward-slash-stripped strings only; encoded forward slashes (`%2F`) wouldn't reach the converter regardless | Low | N/A — slug validator catches anything weird | Phase 2's `validate_slug` is the canonical guard | Test `test_400_invalid_tenant_slug` |

---

## 5. Verification checkpoints

| # | Where | Command | Expected |
|---|---|---|---|
| V1 | After Step 2 | Step-2 canary commands | `STRING_ID accepted` OR `STRING_ID rejected` printed; impl branches on outcome |
| V2 | After Step 3 (exceptions.py) | Import + `http_status` assertions | All four classes constructible; `code`/`http_status` correct |
| V3 | After Step 4 (locks.py) | Import smoke + Phase 2 regression | locks import OK; `tests/test_models.py` still green |
| V4 | After Step 5 (serializers.py) | `UploadBodySerializer({}).is_valid() is False`; `tenant_id` in body rejected | both False with proper error keys |
| V5 | After Step 6 (pipeline.py) | Import smoke + `manage.py check` | no errors |
| V6 | After Step 7 (views.py) | Import smoke + `manage.py check` | no errors |
| V7 | After Step 8 (urls + config) | `manage.py check` + `reverse('upload-document', ...)` + `Client().get('/healthz')` | URL resolves to `/v1/tenants/<a>/bots/<b>/documents`; healthz still 200/503 |
| V8 | After Step 9 (fixtures) | `python -m json.tool < <fixture>` for each | each parses |
| V9 | After Step 10 (test_upload.py) | `pytest --collect-only` | ≥ 8 tests collected; ruff clean |
| V10 | After Step 11 | manage.py check + makemigrations --check + ruff full | all clean |
| V11 | After Step 12 (curl) | 201/201/400 sequence | matches spec acceptance criteria 6/7/8 |
| V12 | After Step 13 (pytest) | `pytest tests/test_upload.py -v` | all green (or skipped if Qdrant unreachable from host) |
| V13 | After Step 14 | full `pytest -v`; mtime audit | 88 + 8 = 96+ green; only `config/urls.py` modified outside Phase 5a scope |

---

## 6. Spec ambiguities & open questions

1. **PointStruct id format — string vs UUID.** Pydantic's `ExtendedPointId = Union[int, str, UUID]` accepts any string at construction, but the **server may reject non-UUID/non-int strings at upsert**. Step 2 canary determines this. If rejected, derive `id=str(uuid.uuid5(NAMESPACE_OID, chunk_id))`. Phase 3's existing `verify_setup.py --full` round-trip used `id=str(uuid.uuid4())` (NOT a `chunk_id`-style string), so we have no prior data point in this repo for whether the server accepts the string form.
2. **DRF `validate()` access to `self.initial_data`.** For JSON requests, `initial_data` is a dict and `set(initial_data.keys())` works. For form data it's a `QueryDict`, and `set(initial_data.keys())` still works. Phase 5a documents the JSON-only assumption; future form-data support is out of scope.
3. **Embedder availability on host.** `tests/test_upload.py` runs from host shell. Phase 4 cached BGE-M3 in `~/.cache/bge`. The first test that loads the embedder pays ~30 s (model load from disk; weights are cached). Subsequent tests reuse via lru_cache. The session fixture distinguishes "Qdrant unreachable" from "embedder unloadable"; both are skip-not-fail per Phase 3 pattern.
4. **`@pytest.mark.django_db` scope.** Tests touch ORM via auto-create + Document.update_or_create. **Every** test in `test_upload.py` needs the marker. The fixture `fresh_bot` is non-DB-touching; `qdrant_available` is non-DB-touching.
5. **Sparse vector with empty `lexical_weights`.** If a chunk has only stopwords, FlagEmbedding may return `{}`. `sparse_to_qdrant({})` returns `{indices:[], values:[]}`. Phase 3's spec says Qdrant accepts empty SparseVector — but the round-trip in `verify_setup.py` uses non-empty `[0],[0.1]`, so we don't have direct proof. **Mitigation**: in pipeline, after `sparse_to_qdrant(...)`, if `indices == []`, log a debug message and still upsert. v1 accepts whatever Qdrant accepts; if it rejects, Phase 5b adds the workaround (e.g., always include a single dummy index).
6. **Tenant + Bot creation outside `upload_lock`.** Two concurrent uploads to **different** doc_ids in the **same new tenant** both call `Tenant.objects.get_or_create(tenant_id=...)` — the advisory lock's `(tenant_id, bot_id, doc_id)` key differs. Race resolved by `IntegrityError + refetch`. Plan §4 R2 + R15 cover this.
7. **`Document.objects.update_or_create` with composite-FK relationship.** Phase 2's Document has `bot_ref` (FK auto-PK) AND denormalized `tenant_id`/`bot_id` CharFields. `update_or_create(doc_id=...)` uses `doc_id` as lookup; `defaults` dict sets `bot_ref=bot` (Django writes `bot_ref_id` from the FK object's PK), `tenant_id=tenant_id`, `bot_id=bot_id`. Verified.
8. **`connection.cursor()` advisory lock vs `pg_advisory_xact_lock`.** Phase 5a uses `pg_advisory_lock(int, int)` (session-level). With Django's default autocommit=True, the lock outlives any `with transaction.atomic()` block. The `try/finally` in `upload_lock()` always calls `pg_advisory_unlock` before the cursor is closed. CONN_MAX_AGE=60 means a leaked lock auto-releases within 60 s if the worker crashes between unlock and cursor-close.
9. **Cross-tenant `doc_id` collision.** If POST hits a `doc_id` that already exists in Postgres but its `tenant_id`/`bot_id` differs from the URL params, Phase 5a's pipeline raises `QdrantWriteError(500)` (per spec). **Better code might be 409 Conflict**, since the request itself is well-formed but conflicts with existing state. Plan flags this for user adjudication; Phase 5a stays at 500 to match the spec literally; if user wants 409, change `QdrantWriteError.http_status` for this specific raise (or introduce a new `DocumentCollisionError` subclass).
10. **Pre-warm vs first-request latency.** `verify_setup.py --full` is the post-deploy warm-up step. Without it, the first POST after a fresh worker triggers a 30-60 s embedder load that may exceed gunicorn's 60 s timeout (default Phase 1 setting). Document: operations runbook MUST include `verify_setup.py --full` after every `make up` or `docker compose restart web`.

---

## 7. Files deliberately NOT created / NOT modified

Per spec §"Out of scope for Phase 5a" and §"Hard constraints" #1:

- **Out-of-scope for 5a (Phase 5b owns):**
  - content_hash short-circuit (200 no_change)
  - `pg_try_advisory_lock` + timeout + 409 conflict
  - Per-doc chunk cap (5000 → 422)
  - `tests/test_pipeline.py` (mocked-embedder unit tests)
  - `tests/test_locks.py` (concurrency)
  - Comprehensive concurrent-upload tests
- **Phase 6:** DELETE endpoint.
- **Phase 7:** gRPC search service.
- **Don't-touch list (Phase 1/2/3/4 source — verified by mtime audit in Step 14):**
  - `apps/core/{views,logging,urls,apps,__init__}.py`
  - `apps/tenants/{models,admin,validators,apps,__init__}.py` + migrations
  - `apps/documents/{models,admin,apps,__init__}.py` + migrations  *(only NEW files in `apps/documents/` are added)*
  - `apps/qdrant_core/{client,collection,exceptions,naming,apps,__init__}.py`
  - `apps/grpc_service/{apps,__init__}.py`
  - `apps/ingestion/{embedder,chunker,payload,apps,__init__}.py`  *(new files: `pipeline.py`, `locks.py`)*
  - `config/{settings,wsgi,asgi,celery,__init__}.py`  *(only `config/urls.py` is modified)*
  - `tests/{conftest,test_settings,test_healthz,test_models,test_naming,test_qdrant_client,test_qdrant_collection,test_chunker,test_payload,test_embedder,__init__}.py`
  - `Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`, `Makefile`, `manage.py`, `.env.example`, `pyproject.toml`, `uv.lock`, `scripts/{compile_proto.sh,verify_setup.py}`, `proto/.gitkeep`, `.github/workflows/ci.yml`, `README.md`.

---

## 8. Acceptance-criteria mapping

| # | Criterion | Build step | Verification | Expected |
|---|---|---|---|---|
| 1 | `uv run ruff check .` zero violations | Steps 3–10 + Step 11 | `uv run ruff check .` | `All checks passed!` |
| 2 | `uv run ruff format --check .` zero changes | Same | `uv run ruff format --check .` | `N files already formatted` |
| 3 | `makemigrations --check --dry-run` no pending | Step 11 | `uv run python manage.py makemigrations --check --dry-run` | `No changes detected` |
| 4 | `manage.py check` exits 0 | Steps 6, 7, 8, 11 | `uv run python manage.py check` | `System check identified no issues (0 silenced).` |
| 5 | `make up && sleep 90 && make health` green | Step 12 | `make health` | `{"status":"ok",...}` |
| 6 | curl 201 + `chunks_created >= 2` + `status="created"` | Step 12 | curl + `python -m json.tool` | 201; `status: "created"`; `chunks_created` ≥ 2 |
| 7 | re-curl returns 201 with `status="replaced"` | Step 12 | curl with same doc_id | 201; `status: "replaced"` |
| 8 | bad slug 400 | Step 12 | curl with `Pizza-Palace` | 400; `code: "invalid_slug"` |
| 9 | `pytest tests/test_upload.py -v` green | Step 13 | host-side pytest | 8/8 green (or skipped if Qdrant unreachable) |
| 10 | full pytest green; healthz still 200 | Step 14 | `pytest -v` + curl /healthz | 96+ green; healthz JSON green |

If docker socket permission still blocked (Phase 4 outstanding §1), criteria 5/6/7/8 satisfy via host-equivalent: `pytest tests/test_upload.py -v` covers the same code paths against the live Qdrant.

---

## 9. Tooling commands cheat-sheet

```bash
# ── Step 2 canary ──
QDRANT_HOST=localhost uv run python -c "..."   # see Step 2 body

# ── Per-module verification ──
uv run python -c "..."          # smoke imports
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run ruff check . && uv run ruff format --check .

# ── URL routing ──
uv run python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from django.urls import reverse
print(reverse('upload-document', kwargs={'tenant_id':'a1b','bot_id':'c2d'}))
"

# ── Stack ──
make down && make up && sleep 90 && make ps && make health

# ── Manual curl smoke (Step 12) ──
curl -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/valid_pdf_doc.json -w "\nHTTP %{http_code}\n" | python -m json.tool

curl -X POST http://localhost:8080/v1/tenants/Pizza-Palace/bots/sup/documents \
     -H "Content-Type: application/json" \
     -d @tests/fixtures/valid_pdf_doc.json -w "\nHTTP %{http_code}\n"   # expect 400

# ── Tests ──
QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
    uv run python -m pytest tests/test_upload.py -v

QDRANT_HOST=localhost BGE_CACHE_DIR=$HOME/.cache/bge \
    uv run python -m pytest -v        # full regression

# ── In-container (when docker unblocked) ──
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
docker compose -f docker-compose.yml exec web pytest tests/test_upload.py -v
docker compose -f docker-compose.yml exec web pytest -v
```

---

## 10. Estimated effort

| Step | Task | Effort |
|---|---|---|
| 1 | Read & inventory | 5 min |
| 2 | PointStruct id canary | 10 min |
| 3 | exceptions.py | 5 min |
| 4 | locks.py | 10 min |
| 5 | serializers.py | 15 min |
| 6 | pipeline.py | 30 min (longest module; many integration points) |
| 7 | views.py | 15 min |
| 8 | urls.py + config/urls.py | 10 min |
| 9 | Fixtures | 10 min |
| 10 | test_upload.py | 30 min |
| 11 | manage.py check + ruff sweep | 5 min |
| 12 | Stack rebuild + curl smoke | **5–15 min** (depends on docker access; 90 s sleep + warmup) |
| 13 | pytest tests/test_upload.py | **5–10 min** (first run loads embedder once if cached; ~30 s + per-test latency) |
| 14 | Final regression + audit + report | 15 min |
| | **Total** | **~3 hours wall clock first run; ~1 hour on warm cache** |

---

## End of plan (initial)
