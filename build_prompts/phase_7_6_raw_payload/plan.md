# Phase 7.6 — Implementation Plan (revised)

> Produced by Prompt 1 (PLAN), revised by Prompt 2 (REVIEW). Inputs: Phase 7.6 spec.md, Phases 5/7.5 specs + reports, current state of source files, `plan_review.md`.

---

## 0. Revision notes

This plan is revision 2. Findings from `plan_review.md`:

- **F1 [major]:** Migration auto-name acceptable — plan §6 A3 already documents fallback if Django picks a non-spec name. No plan change needed.
- **F2 [major]:** Admin null placeholder uses spec-literal `if obj.raw_payload is None: return "—"` (NOT `if not obj.raw_payload`). Empty dicts aren't reachable in practice (UploadBodySerializer enforces non-empty items); spec-literal form is safer for future-proofing.
- **F3 [major]:** Existing `_body()` helper compat confirmed; plan §3.6 overrides `content_hash` per-test.
- **F4-F12 minors** noted for documentation only.

Zero critical findings. Plan proceeds.

---

## 1. Plan summary

Phase 7.6 adds a single `raw_payload` `JSONField(null=True, blank=True)` to `Document`, persists the validated upload body (post-DRF defaults) on the create/replace branch of the pipeline only, exposes it as a pretty-printed read-only `<pre>` block in the Django admin, and ships one `AddField` migration. The riskiest pieces are: (a) the `raw_payload` write must NOT live in a "shared defaults" block that the two `no_change` short-circuits also flow through — both no_change branches must return early WITHOUT touching `raw_payload`; (b) the migration generated on host (`make makemigrations-host APP=documents`) must capture *only* the new field, not any unrelated drift, and must land in git/the working tree before `make rebuild` so the image bakes in 0002 alongside the model edit. The build verifies itself via: model-introspection check for the new field + null/blank flags, migration-file inspection (one `AddField` op, parent `0001_initial`), `manage.py check`, three new pipeline tests covering the create/no_change/replace matrix, full Phase 1-7.5 regression, and a manual admin smoke (upload via curl → admin shows pretty-printed JSON).

---

## 2. Build order & dependency graph

| # | Artifact | Depends on | Why |
|---|---|---|---|
| 1 | `apps/documents/models.py` | — | Add `raw_payload = models.JSONField(null=True, blank=True, help_text=...)`. |
| 2 | `make makemigrations-host APP=documents` | 1 | Generates `apps/documents/migrations/0002_document_raw_payload.py` on the HOST filesystem (uses tests/test_settings.py SQLite overlay; no DB connection needed). |
| 3 | Migration introspection | 2 | `cat apps/documents/migrations/0002_*.py` must show ONE `AddField` op with `parent_dependency=[('documents', '0001_initial')]`. If anything else is in there → abort, `git diff` to find drift. |
| 4 | `apps/ingestion/pipeline.py` | 1 | Add `"raw_payload": body` to the `update_or_create(defaults={...})` block ONLY (the create/replace branch). The two no_change short-circuits at lines 128-188 (current state) DO NOT touch `raw_payload`. |
| 5 | `apps/documents/admin.py` | 1 | Add `_raw_payload_pretty(obj)` method, `exclude = ("raw_payload",)`, append to `readonly_fields`, define `fields` ordering so the pretty block appears at the end. |
| 6 | `tests/test_pipeline.py` | 1, 4 | Add `TestRawPayloadPersistence` class with 3 tests reusing `_body()`, `_doc_id()`, `mock_embedder` fixtures. |
| 7 | `manage.py check` + targeted pytest | 1-6 | Sanity check imports; run `tests/test_pipeline.py -v` for fast feedback. |
| 8 | Stack rebuild | 1-6 | `make down && make rebuild && sleep 90 && make ps && make health`. The `web` container's CMD already runs `migrate --noinput` on startup → applies 0002 in the production stack's Postgres. |
| 9 | Manual admin smoke | 8 | curl POST a doc → open `/admin/documents/document/<doc_id>/change/` → verify pretty-printed JSON renders. Re-upload same body → admin still shows v1. Re-upload with same doc_id+different content → admin shows v2. |
| 10 | Phase 1-7.5 regression | 8 | `make run pytest -v` AND host `uv run pytest -v` both green. |

Notes:
- Steps 4 and 5 are independent (pipeline write vs admin render); could run in parallel. Step 6 depends on both.
- Step 2 explicitly uses `make makemigrations-host` — the spec pitfall #8 forbids running `makemigrations` inside the container (the file lives in container fs and is lost on rebuild). makemigrations-host writes to `apps/documents/migrations/` directly on the host filesystem.
- The migration file goes through the same image-build pipeline as any source: `COPY . .` in Dockerfile picks it up; `migrate --noinput` in compose web command applies it on startup.

---

## 3. Build steps (sequenced)

### Step 3.1 — Edit `apps/documents/models.py`

- **Goal:** Add `raw_payload` field.
- **Diff:** insert one field after `last_refreshed_at`:
  ```python
  raw_payload = models.JSONField(
      null=True,
      blank=True,
      help_text=(
          "Validated upload body as posted by the scraper. "
          "Debug aid only — do not read this for ingestion / chunking. "
          "Set on create/replace; untouched on no_change."
      ),
  )
  ```
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.documents.models import Document
  f = Document._meta.get_field('raw_payload')
  assert f.null and f.blank
  assert f.get_internal_type() == 'JSONField'
  print('raw_payload field ok')
  "
  ```
- **Rollback:** delete the new field block.
- **Estimated effort:** 5 min.

### Step 3.2 — Generate migration (host)

- **Goal:** create `apps/documents/migrations/0002_document_raw_payload.py`.
- **Command:** `make makemigrations-host APP=documents`.
- **Why host (not container):** the file must live on the host filesystem before `make rebuild` so `COPY . .` in the Dockerfile bakes it into the image. `make makemigrations` (in-container) would write to the container's fs and be lost on rebuild.
- **Why this works without a DB:** `tests/test_settings.py` overlays SQLite in-memory; makemigrations operates on model state, not data. No DB connection required.
- **Verification:**
  ```
  ls apps/documents/migrations/
  # expect: 0001_initial.py 0002_document_raw_payload.py __init__.py
  ```
- **Rollback:** `rm apps/documents/migrations/0002_document_raw_payload.py`.
- **Estimated effort:** 2 min.

### Step 3.3 — Inspect generated migration

- **Goal:** confirm only `AddField` op, correct field params, parent is `0001_initial`.
- **Command:** `cat apps/documents/migrations/0002_document_raw_payload.py`.
- **Expected shape:**
  ```python
  class Migration(migrations.Migration):
      dependencies = [("documents", "0001_initial")]
      operations = [
          migrations.AddField(
              model_name="document",
              name="raw_payload",
              field=models.JSONField(blank=True, null=True, help_text="..."),
          ),
      ]
  ```
- **Abort condition:** anything else (a second AddField, an AlterField, an index op) → revert step 3.1, investigate drift.
- **Estimated effort:** 1 min.

### Step 3.4 — Edit `apps/ingestion/pipeline.py`

- **Goal:** write `body` to `Document.raw_payload` ONLY on create/replace.
- **Diff:** locate the existing `Document.objects.update_or_create(defaults={...})` block (current line ~250). Add `"raw_payload": body,` to the defaults dict. NO change anywhere near the two no_change return paths (around lines 128-188 of the current pipeline.py — the same-doc_id+same-hash branch and the cross-doc_id content match branch).
- **CRITICAL:** the two no_change branches MUST remain `return UploadResult(...)` early WITHOUT mutating any Document field other than `last_refreshed_at` (already done by `existing.save(update_fields=["last_refreshed_at"])`). If `raw_payload` was inadvertently in a shared "default-defaults" assignment, both no_change paths would bypass it (they don't reach update_or_create) — but plan keeps the write strictly inside `update_or_create.defaults` so accidental movement to a shared block would fail tests immediately.
- **Verification:**
  ```
  uv run python manage.py check
  grep -n "raw_payload" apps/ingestion/pipeline.py
  # expect: ONE hit, inside the defaults={} block of the update_or_create call
  ```
- **Rollback:** remove the line.
- **Estimated effort:** 5 min.

### Step 3.5 — Edit `apps/documents/admin.py`

- **Goal:** show `raw_payload` as pretty-printed read-only JSON.
- **Diff:**
  ```python
  import json

  from django.contrib import admin
  from django.utils.html import format_html

  from apps.documents.models import Document


  @admin.register(Document)
  class DocumentAdmin(admin.ModelAdmin):
      list_display = (
          "doc_id", "tenant_id", "bot_id",
          "source_filename", "source_type", "status",
          "chunk_count", "uploaded_at",
      )
      list_filter = ("status", "source_type", "tenant_id")
      search_fields = ("doc_id", "source_filename", "source_url")
      ordering = ("-uploaded_at",)
      exclude = ("raw_payload",)
      readonly_fields = (
          "doc_id",
          "uploaded_at",
          "last_refreshed_at",
          "chunk_count",
          "item_count",
          "raw_payload_pretty",
      )

      @admin.display(description="Raw payload (uploaded JSON)")
      def raw_payload_pretty(self, obj):
          if obj.raw_payload is None:
              return "—"
          rendered = json.dumps(obj.raw_payload, indent=2, ensure_ascii=False)
          return format_html(
              '<pre style="white-space: pre-wrap; max-height: 600px; overflow: auto; '
              'background: #f8f8f8; padding: 12px; border: 1px solid #ddd; '
              'border-radius: 4px; font-family: monospace; font-size: 12px;">{}</pre>',
              rendered,
          )
  ```
- **Why `exclude` + `readonly_fields` callable:** spec pitfall #3 — referencing the editable `raw_payload` field directly in `readonly_fields` would still render an editable widget. Excluding the field, then surfacing a derived display callable, is the canonical Django admin pattern.
- **Verification:**
  ```
  uv run python -c "
  import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  import django; django.setup()
  from apps.documents.admin import DocumentAdmin
  assert 'raw_payload' in DocumentAdmin.exclude
  assert 'raw_payload_pretty' in DocumentAdmin.readonly_fields
  assert callable(getattr(DocumentAdmin, 'raw_payload_pretty'))
  print('admin ok')
  "
  ```
- **Rollback:** restore Phase 7.5 admin.py.
- **Estimated effort:** 10 min.

### Step 3.6 — Add tests to `tests/test_pipeline.py`

- **Goal:** assert the create/no_change/replace matrix.
- **Diff:** append a new class beside `TestContentHashShortCircuit`:
  ```python
  @pytest.mark.django_db
  class TestRawPayloadPersistence:
      def test_raw_payload_persists_full_body(self, mock_embedder):
          d = _doc_id()
          body = _body(items_count=1)
          UploadPipeline.execute(tenant_id="rp_a", bot_id="rp_b", doc_id=d, body=body)
          row = Document.objects.get(doc_id=d)
          assert row.raw_payload is not None
          assert row.raw_payload == body

      def test_raw_payload_unchanged_on_no_change(self, mock_embedder):
          d = _doc_id()
          body = _body(items_count=1, content_hash="sha256:nochange-7-6")
          UploadPipeline.execute(tenant_id="rp_a2", bot_id="rp_b2", doc_id=d, body=body)
          v1 = Document.objects.get(doc_id=d).raw_payload
          assert v1 == body

          r2 = UploadPipeline.execute(tenant_id="rp_a2", bot_id="rp_b2", doc_id=d, body=body)
          assert r2.status == "no_change"
          assert Document.objects.get(doc_id=d).raw_payload == v1

      def test_raw_payload_overwritten_on_replace(self, mock_embedder):
          d = _doc_id()
          body1 = _body(items_count=1, content_hash="sha256:rp-v1")
          UploadPipeline.execute(tenant_id="rp_a3", bot_id="rp_b3", doc_id=d, body=body1)
          body2 = _body(items_count=1, content_hash="sha256:rp-v2")
          body2["items"][0]["content"] = "Replaced content for v2 of this raw_payload test."
          r2 = UploadPipeline.execute(tenant_id="rp_a3", bot_id="rp_b3", doc_id=d, body=body2)
          assert r2.status == "replaced"
          row = Document.objects.get(doc_id=d)
          assert row.raw_payload == body2
          assert row.raw_payload["items"][0]["content"] == body2["items"][0]["content"]
  ```
- **Test isolation note:** each test uses unique `(tenant_id, bot_id)` slugs (`rp_a`/`rp_a2`/`rp_a3`) so the cross-doc_id content match dedup from Phase 7.5 cannot fire across tests. Each also uses unique content_hash where the dedup matters.
- **Verification:** `make run pytest tests/test_pipeline.py -v` (or host equivalent with mocked embedder).
- **Rollback:** delete the new class.
- **Estimated effort:** 20 min.

### Step 3.7 — Stack rebuild + smoke

- **Goal:** image bakes in 0002 migration; container migrate runs it; admin renders correctly.
- **Commands:**
  ```
  make down
  make rebuild
  sleep 90
  make ps
  make health
  make run python manage.py showmigrations documents
  ```
  Expected: `[X] 0002_document_raw_payload`.
- **Manual smoke (host curl):**
  ```
  curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
       -H "Content-Type: application/json" \
       -d '{"items":[{"content":"raw payload smoke test."}]}' \
       -w "\nHTTP %{http_code}\n"
  # Expect 201; capture doc_id from response
  ```
  Open `http://localhost:8080/admin/documents/document/<doc_id>/change/` → "Raw payload (uploaded JSON)" section shows the pretty JSON.
- **Estimated effort:** 10 min.

### Step 3.8 — Migration reversibility check

- **Goal:** spec acceptance criterion 10.
- **Commands:**
  ```
  make run python manage.py migrate documents 0001_initial
  make run python manage.py showmigrations documents
  # expect: [X] 0001_initial, [ ] 0002_document_raw_payload
  make run python manage.py migrate documents
  make run python manage.py showmigrations documents
  # expect: [X] 0001_initial, [X] 0002_document_raw_payload
  ```
- **Estimated effort:** 5 min.

### Step 3.9 — Phase 1-7.5 regression

- **Commands:**
  ```
  make run pytest -v
  uv run pytest -v   # host (skip-graceful for embedder)
  uv run ruff check .
  uv run ruff format --check .
  ```
- **Expected:** all prior tests still green; +3 new tests in test_pipeline.py.
- **Estimated effort:** 5-10 min.

### Step 3.10 — Implementation report

- Out-of-scope for this plan; Prompt 3 generates `build_prompts/phase_7_6_raw_payload/implementation_report.md`.

---

## 4. Risk register

### R1 [critical] — `raw_payload` accidentally written on no_change branches
The pipeline has TWO no_change short-circuits (same-doc_id+same-hash AND cross-doc_id content match — Phase 7.5 added the second). Both `existing.save(update_fields=["last_refreshed_at"])` early. If a future refactor extracts a "shared defaults" block and uses `update_fields=["last_refreshed_at", "raw_payload"]` or moves the write up, both branches would silently overwrite. Spec hard constraint #8 + tests guard.

**Mitigation:** keep the `"raw_payload": body` line strictly inside the `update_or_create(defaults={...})` block. Test 2 (`test_raw_payload_unchanged_on_no_change`) catches a regression immediately.

### R2 [critical] — Migration applied inside container but not committed to host
spec pitfall #8. Generating the migration via `make makemigrations APP=documents` (in-container) writes to container's fs, lost on rebuild. Plan §3.2 explicitly uses `make makemigrations-host` so the file lands on the host filesystem.

**Mitigation:** Step 3.2 uses host invocation; step 3.3 verifies the file exists on host via `ls`.

### R3 [major] — Generated migration captures unrelated drift
If `models.py` has any unsaved drift from prior aborted edits, `makemigrations` may include unrelated `AlterField` / index ops in the same migration. Plan §3.3 inspects the generated file before proceeding.

**Mitigation:** explicit cat + abort if anything beyond `AddField` for `raw_payload` appears.

### R4 [major] — Admin double-renders raw_payload
Spec pitfall #3. Plan §3.5 uses `exclude = ("raw_payload",)` AND references only the callable `raw_payload_pretty` in `readonly_fields`. Without `exclude`, Django admin would render an editable widget alongside the readonly callable.

**Mitigation:** explicit `exclude` in admin; verification import-checks both attributes.

### R5 [major] — `format_html` + `ensure_ascii=False` both required
Spec hard constraint #6 + pitfalls #5/#6. Plan §3.5 uses `format_html(...{})` (HTML-escapes the JSON-text argument) AND `json.dumps(..., ensure_ascii=False)` (preserves non-ASCII). Forgetting `format_html` is an XSS vector via crafted JSON values; forgetting `ensure_ascii=False` shows `é` instead of `é`.

**Mitigation:** plan §3.5 spells out both calls.

### R6 [major] — Test fixture pollution from cross-doc_id dedup (Phase 7.5)
Phase 7.5's pipeline includes a cross-doc_id content_hash short-circuit. If two tests upload the same content with the same `content_hash` to the same `(tenant_id, bot_id)`, the second upload returns `no_change` against the first test's row, regardless of doc_id.

**Mitigation:** plan §3.6 gives each test a unique `(tenant_id, bot_id)` slug pair (`rp_a`/`rp_a2`/`rp_a3`). pytest-django's transactional rollback also gives each `@pytest.mark.django_db` test its own transaction by default, but the unique slugs are belt-and-suspenders.

### R7 [minor] — DRF default application timing
The pipeline receives `body = serializer.validated_data` (post-DRF defaults; e.g., `source_type="text"` if omitted by caller). Spec hard constraint #3 confirms this is the desired write target. `validated_data` is a `dict`-like object; `JSONField` accepts it. No coercion needed.

### R8 [minor] — Postgres jsonb storage ceiling
Per project memory ("storage is not a concern"), large web-scraped payloads are expected and not truncated. Postgres jsonb has a practical ceiling around 100 MB per row before `pg_dump` and replication begin to suffer. v1 acceptable; document for Phase 8 to add a soft-cap warning if needed.

### R9 [minor] — Admin page render time for very large payloads
A 50 MB `raw_payload` rendered in `<pre>` produces a 50 MB HTML response and freezes the browser. The `max-height: 600px; overflow: auto` CSS limits the *visible* area but not the response size. v1 accepts this; spec Out-of-scope explicitly defers truncation.

### R10 [minor] — `body` mutation between dedup checks and create/replace
The pipeline doesn't mutate `body` between the no_change short-circuit and the create/replace branch. Plan §3.4 doesn't introduce any mutation. If a future refactor adds normalization (e.g., dropping unknown keys), it should mutate a *copy* and pass the copy to `update_or_create.defaults["raw_payload"]`, not the original input — but for v1, no-mutation invariant holds.

### R11 [minor] — `raw_payload` exposure in API responses
The HTTP upload view returns `{doc_id, chunks_created, items_processed, collection_name, status}` — no `raw_payload`. The HTTP search view returns Qdrant chunks (payload dicts), not `Document.raw_payload`. The DELETE view returns 204. No leak path. v1 acceptable.

### R12 [minor] — Migration filename race
If two devs both run `makemigrations` simultaneously, both produce `0002_*.py` — the second one to commit forces a renumber. Single-dev v1 workflow; not a practical risk.

---

## 5. Verification checkpoints

| # | Checkpoint | Command | Expected |
|---|---|---|---|
| 5.1 | Field exists with correct null/blank | `Document._meta.get_field('raw_payload')` returns JSONField, null=True, blank=True | imports clean; assertions pass |
| 5.2 | Migration file generated on host | `ls apps/documents/migrations/` | shows `0002_document_raw_payload.py` |
| 5.3 | Migration body is one AddField | `cat apps/documents/migrations/0002_*.py` | one operation, parent `0001_initial`, field params match |
| 5.4 | `manage.py check` clean | `uv run python manage.py check` | exit 0 |
| 5.5 | Pipeline writes `raw_payload` exactly once | `grep -n "raw_payload" apps/ingestion/pipeline.py` | 1 hit, inside `update_or_create(defaults={...})` |
| 5.6 | Admin attrs correct | Python introspection of `DocumentAdmin.exclude`/`readonly_fields` | `raw_payload` in exclude; `raw_payload_pretty` in readonly_fields |
| 5.7 | Pipeline tests green | `make run pytest tests/test_pipeline.py -v` | all + 3 new pass |
| 5.8 | Stack rebuild + showmigrations | `make rebuild && sleep 90 && make run python manage.py showmigrations documents` | `[X] 0002_document_raw_payload` |
| 5.9 | Manual admin smoke | curl POST + admin URL | pretty-printed JSON renders; null shows `—` |
| 5.10 | Migration reversibility | `migrate 0001_initial` then `migrate` | unapplied then re-applied without error |
| 5.11 | Phase 1-7.5 regression | `make run pytest -v` + `uv run pytest -v` | all prior green + 3 new |
| 5.12 | Lint + format | `uv run ruff check . && uv run ruff format --check .` | no violations / no diffs |
| 5.13 | Idempotent makemigrations | `make makemigrations-host APP=documents` after step 3.2 | "No changes detected" |

---

## 6. Spec ambiguities & open questions

### A1 — Test isolation under pytest-django's transactional rollback
`@pytest.mark.django_db` (default) wraps each test in a transaction and rolls back at teardown. Cross-test pollution from the Phase 7.5 cross-doc_id dedup is contained per-test. Plan §3.6 still uses unique `(tenant_id, bot_id)` slugs as belt-and-suspenders against any future fixture-scope shift.

### A2 — Empty-dict admin display
Spec doesn't specify what `obj.raw_payload == {}` renders as. Plan reads the spec literally: only `is None` returns `—`; an empty dict renders as `{}` (two-character pretty JSON in the `<pre>` block). Acceptable.

### A3 — Migration filename
Spec says `0002_document_raw_payload.py`. Django's auto-namer typically generates `0002_document_raw_payload.py` (model name + field name). If Django chooses a different name (e.g., `0002_alter_document_raw_payload.py`), accept the auto-generated name and document in the implementation report.

### A4 — `body` post-DRF default values vs. request data
Spec hard constraint #3 settles this: `body` is `serializer.validated_data` (post-defaults), not `request.data` (raw). Plan §3.4 writes `body` directly without re-coercion.

### A5 — Postgres jsonb size ceiling
~100 MB per row practical. Spec defers truncation explicitly; out-of-scope per project memory ("storage is not a concern"). Plan §R8 documents.

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope — never touched

- `apps/core/`, `apps/tenants/`, `apps/qdrant_core/`, `apps/grpc_service/`, `apps/ingestion/{embedder,chunker,payload,locks}.py`
- `apps/documents/{serializers,views,urls,exceptions}.py` — no upload schema or routing changes
- `apps/documents/migrations/0001_initial.py` — preserved verbatim
- `config/{settings,urls,wsgi,asgi,celery}.py`
- `proto/search.proto`, `apps/grpc_service/generated/*` (generated)
- `Dockerfile`, `docker-compose.yml`, `Makefile`, `pyproject.toml`, `uv.lock`
- `scripts/compile_proto.sh`, `scripts/verify_setup.py`
- `tests/test_{healthz,models,naming,qdrant_client,qdrant_collection,chunker,embedder,locks,delete,upload,payload,search_grpc,search_query,search_http}.py` and `conftest.py`
- `tests/fixtures/valid_pdf_doc.json`
- `README.md`, `rag_system_guide.md`, `MEMORY.md`

### Phase 7.6 explicit modifies (3) + new (2)

- **Modified:** `apps/documents/models.py`, `apps/ingestion/pipeline.py`, `apps/documents/admin.py`, `tests/test_pipeline.py`.
- **New:** `apps/documents/migrations/0002_document_raw_payload.py`, `build_prompts/phase_7_6_raw_payload/implementation_report.md` (Prompt 3 task).

(Spec lists "5 files modified or new" — counted as 4 modified + 1 generated migration = 5; the report file is meta.)

---

## 8. Acceptance-criteria mapping

| # | Criterion | Step | Verify | Expected |
|---|---|---|---|---|
| 1 | `Document.raw_payload` exists with `JSONField(null=True, blank=True)` | 3.1 | step 5.1 | introspection asserts pass |
| 2 | Migration `0002_document_raw_payload` committed and applies cleanly | 3.2, 3.3, 3.7 | step 5.2, 5.3, 5.8 | file on host; one AddField; `[X] 0002` after rebuild |
| 3 | Pipeline writes `raw_payload` on create/replace | 3.4 | step 5.5, 5.7 (test 1 + test 3) | 1 grep hit; tests green |
| 4 | Pipeline does NOT touch `raw_payload` on no_change | 3.4 | step 5.7 (test 2) | test 2 green |
| 5 | Admin renders pretty JSON on populated; `—` on null | 3.5 | step 5.6 + step 5.9 | exclude/readonly_fields set; manual smoke |
| 6 | No upload regressions | 3.4, 3.6 | step 5.11 | `tests/test_upload.py` green |
| 7 | No search regressions | (no changes to search) | step 5.11 | `tests/test_search_*.py` green |
| 8 | Phase 1-7.5 regression | (full suite) | step 5.11 | all prior green |
| 9 | Manual smoke (upload→admin→re-upload→admin) | 3.7 | step 5.9 | three-way smoke succeeds |
| 10 | Migration reversibility | 3.8 | step 5.10 | apply/reverse/re-apply clean |
| 11 | Stack health post-rebuild | 3.7 | step 5.8 | `make ps` shows 6 healthy/running containers |

---

## 9. Tooling commands cheat-sheet

```bash
# Migration generation (HOST — file lands on host fs, persists across rebuild)
make makemigrations-host APP=documents
ls apps/documents/migrations/
cat apps/documents/migrations/0002_*.py

# Sanity
uv run python manage.py check
uv run ruff check .
uv run ruff format --check .

# Tests (host with mocked embedder)
uv run pytest tests/test_pipeline.py -v

# Inside-container (preferred — production stack with real Postgres)
make run pytest tests/test_pipeline.py -v
make run pytest -v
make run python manage.py showmigrations documents
make run python manage.py migrate documents 0001_initial
make run python manage.py migrate documents

# Stack
make down
make rebuild
sleep 90
make ps
make health

# Manual smoke
curl -sS -X POST http://localhost:8080/v1/tenants/test_t/bots/test_b/documents \
     -H "Content-Type: application/json" \
     -d '{"items":[{"content":"raw payload smoke."}]}' \
     -w "\nHTTP %{http_code}\n"
# open http://localhost:8080/admin/documents/document/<doc_id>/change/

# Idempotent makemigrations check (no drift)
make makemigrations-host APP=documents
# expect: "No changes detected in app 'documents'"

# Out-of-scope mtime audit (no git)
find apps/documents/{serializers,views,urls,exceptions}.py \
     apps/ingestion/{embedder,chunker,payload,locks}.py \
     apps/grpc_service apps/qdrant_core apps/tenants apps/core \
     proto config Dockerfile docker-compose.yml Makefile pyproject.toml uv.lock \
     scripts \
     -newer build_prompts/phase_7_5_api_cleanup/implementation_report.md \
     2>/dev/null
# expect empty
```

---

## 10. Estimated effort

| Step | Estimate |
|---|---|
| 3.1 models.py field | 5 min |
| 3.2 makemigrations-host | 2 min |
| 3.3 migration inspection | 1 min |
| 3.4 pipeline.py write | 5 min |
| 3.5 admin.py pretty-print | 10 min |
| 3.6 test_pipeline.py +3 tests | 20 min |
| 3.7 stack rebuild + smoke | 10 min |
| 3.8 migration reversibility | 5 min |
| 3.9 regression | 10 min |
| 3.10 implementation_report.md (Prompt 3) | 20 min |
| **Total** | **~1.5 hours** |

---

## End of plan
