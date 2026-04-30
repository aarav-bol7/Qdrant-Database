# Phase 7.6 — Raw Payload Persistence

> **Audience:** A coding agent building on top of verified-green Phases 1–7.5 at `/home/bol7/Documents/BOL7/Qdrant`. Phase 7.6 is a small focused feature addition that sits between Phase 7.5 and Phase 8.

---

## Mission

Persist the full upload request body on the `Document` row so an operator browsing the Django admin can see *exactly what came in from the scraper* alongside what got chunked and indexed.

After Phase 7.6: every successful upload writes the request body (the `body` dict that the DRF serializer parsed and validated) to a new `Document.raw_payload` JSON column. The Django admin renders this payload as a pretty-printed, scrollable, read-only block in the document detail view. No schema changes to the upload API. No changes to search, gRPC, chunker, embedder, or any algorithm. One Postgres migration. ~5-7 files modified, ~3 source edits + 1 admin tweak + 1 migration + 2 tests.

---

## Why now (and why not folded into Phase 8)

- The user has been tuning chunker quality and wants visibility into "what was uploaded vs. what got chunked." Currently the only way to inspect uploaded content is via Qdrant payload (chunked text only, no source-level context).
- Phase 8 (Hardening & Ship) is large and concerned with observability/deployment. Mixing this small feature into Phase 8 would muddle Phase 8's verification criteria.
- This is its own phase with its own clean spec → plan → review → implement audit trail.

---

## Read first

- `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's upload contract. Unchanged in this phase; the new `raw_payload` field is downstream of validation.
- `build_prompts/phase_5b_upload_idempotency/spec.md` — content_hash short-circuit + advisory lock. The `no_change` and `replaced` paths interact with `raw_payload`; spec below pins behavior.
- `build_prompts/phase_7_5_api_cleanup/spec.md` — current upload schema (1 required + 5 optional). Unchanged.
- `build_prompts/phase_7_5_api_cleanup/implementation_report.md` — Phase 7.5 outcomes. The pipeline now also auto-computes `content_hash` server-side and dedups across `doc_id`s; that logic is unchanged here.
- `apps/documents/models.py` — current `Document` model.
- `apps/documents/admin.py` — current admin config.
- `apps/ingestion/pipeline.py` — current upload pipeline.
- `README.md` — context.

---

## Hard constraints

1. **One new column.** `Document.raw_payload` is a `JSONField(null=True, blank=True)`. No additional fields. No size cap. No truncation.
2. **No upload schema changes.** The DRF serializers (`UploadBodySerializer`, `UploadItemSerializer`, etc.) and the URL routes are not touched.
3. **Persist the validated body, not the raw HTTP request.** The pipeline already receives `body: dict` — that's what gets written. This means `validated_data` post-DRF-validation, NOT `request.data` raw, NOT the raw request bytes. (Subtle: validated_data has DRF's defaults applied, e.g. `source_type` → `"text"` if omitted; that's fine and arguably more useful than the raw input.)
4. **Migration must be reversible.** Standard Django `AddField` is reversible by default; do not block reverse migrations.
5. **No backfill.** Documents uploaded before this migration get `raw_payload = NULL`. The admin renders `—` for them. Do not write a data migration to backfill from elsewhere.
6. **Admin must render `raw_payload` as readonly pretty-printed JSON.** Not editable. Bound to a max-height scroll box (~600px) with `overflow: auto` so very large payloads don't take over the form. Field label: "Raw payload (uploaded JSON)".
7. **`raw_payload` must be excluded from the editable form.** The admin shows the *pretty-printed* version (a derived callable) in `readonly_fields`; the underlying `raw_payload` field is added to `exclude` so Django doesn't try to render an editable widget for it.
8. **Dedup hot paths leave `raw_payload` untouched.** When the pipeline returns `status="no_change"` (either same-doc_id+same-hash OR cross-doc_id content match), the existing Document's `raw_payload` is NOT updated. Reasoning: `no_change` semantically means "nothing happened; the existing row is already authoritative."
9. **`status="replaced"` overwrites `raw_payload`.** Same-doc_id with different content re-runs the full pipeline; the new body replaces the old. This happens automatically through `update_or_create(defaults={"raw_payload": body, ...})`.
10. **JSON-native types only.** The `body` dict from DRF is already JSON-native (str, int, list, dict, bool, None). No special encoder needed.
11. **No new dependencies.** Django's stdlib + `json.dumps` + `format_html` — already available.
12. **Tests at minimum:** one positive (raw_payload persisted on create), one verification of the no_change rule (raw_payload unchanged on no-op re-upload), and one verification of the replaced rule (raw_payload reflects v2 after same-doc_id update).

---

## Files modified / created

**Modified:**
1. `apps/documents/models.py` — add `raw_payload` field
2. `apps/documents/admin.py` — add pretty-print callable + readonly_fields + exclude
3. `apps/ingestion/pipeline.py` — add `"raw_payload": body` to the `update_or_create` defaults block (the create/replace branch only — NOT the no_change short-circuit branches)

**New (auto-generated):**
4. `apps/documents/migrations/0002_document_raw_payload.py` — generated by `makemigrations`. Single `AddField` operation.

**Modified (tests):**
5. `tests/test_pipeline.py` OR `tests/test_upload.py` — add the 3 tests below

---

## Behavior — exact contract

### Upload paths

| Pipeline path | `raw_payload` write behavior |
|---|---|
| `status="created"` (new doc, full pipeline) | Set to validated `body` |
| `status="replaced"` (same doc_id, different content) | Set to validated `body` (overwrites previous) |
| `status="no_change"` via doc_id match | **Not touched** |
| `status="no_change"` via cross-doc_id content match | **Not touched** |

### Admin rendering

- `raw_payload` field is in `exclude` (no editable widget)
- `raw_payload_pretty` (callable) is in `readonly_fields` and `fields` ordering
- The callable returns:
  - `"—"` if `obj.raw_payload is None`
  - `format_html('<pre style="...">{}</pre>', json.dumps(obj.raw_payload, indent=2, ensure_ascii=False))` otherwise
- The `<pre>` block has CSS: `white-space: pre-wrap; max-height: 600px; overflow: auto; background: #f8f8f8; padding: 12px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 12px;`
- `format_html` ensures the JSON content is HTML-escaped (no XSS via crafted JSON values).

### Migration

- File: `apps/documents/migrations/0002_document_raw_payload.py`
- Single operation: `migrations.AddField(model_name='document', name='raw_payload', field=models.JSONField(blank=True, null=True))`
- Reversible (standard for `AddField`).
- Applied automatically on container startup via `python manage.py migrate --noinput` (already in compose `web` command — Phase 1 baseline).

---

## Tests

### Required tests

**Test 1 — `test_raw_payload_persists_full_body`**
Upload via the pipeline. Fetch the resulting Document. Assert `Document.raw_payload == body` (the validated body dict — including DRF-applied defaults like `source_type="text"` if omitted by caller).

**Test 2 — `test_raw_payload_unchanged_on_no_change`**
Upload v1. Capture `Document.raw_payload`. Upload identical content again (same doc_id). Assert `status == "no_change"` and `Document.raw_payload` is byte-identical to v1's (specifically: not `None`, equal to the v1 body).

**Test 3 — `test_raw_payload_overwritten_on_replace`**
Upload v1. Upload v2 with same doc_id but different `items[0].content`. Assert `status == "replaced"` and `Document.raw_payload["items"][0]["content"]` equals v2's content (not v1's).

### Test placement

`tests/test_pipeline.py` — beside the existing `TestContentHashShortCircuit` class. Add a new `TestRawPayloadPersistence` class with the 3 tests above. Reuse `_body()` and `_doc_id()` helpers.

### Test isolation
- Tests use `mock_embedder` fixture (already present) to skip BGE-M3
- Tests use the existing autouse SQLite-friendly mocks (no Postgres advisory lock contention on host runs)

---

## Acceptance criteria

1. **`Document.raw_payload` exists** with type `JSONField(null=True, blank=True)`, verified by `python manage.py inspectdb` or model introspection.
2. **Migration `0002_document_raw_payload`** is committed and applies cleanly via `make rebuild`.
3. **Pipeline writes `raw_payload`** on the create/replace branch, verified by a test.
4. **Pipeline does NOT touch `raw_payload`** on either no_change branch, verified by a test.
5. **Admin change form for a Document** shows a "Raw payload (uploaded JSON)" section with pretty-printed scrollable JSON when `raw_payload` is populated; shows `—` when null.
6. **No upload schema regressions:** `make run pytest tests/test_upload.py -v` stays green.
7. **No search regressions:** `make run pytest tests/test_search_query.py tests/test_search_grpc.py tests/test_search_http.py -v` stays green.
8. **Phase 1-7.5 regression:** full test suite stays green.
9. **Manual smoke:**
   - Upload a doc via curl → admin shows the JSON.
   - Re-upload same body → status `no_change` → admin still shows the original JSON.
   - Re-upload with same doc_id and different items → status `replaced` → admin shows the new JSON.
10. **Migration reversibility:** `python manage.py migrate documents 0001_initial` followed by `python manage.py migrate` works (apply, reverse, re-apply) without error.
11. **Stack health:** `make ps` shows all 6 containers healthy after `make rebuild`.

---

## Common pitfalls

1. **Pipeline writes `raw_payload` on the no_change branches.** Tempting to put it in a "centralized" defaults block that all paths share. Don't — keep it confined to `update_or_create` defaults. The two no_change branches return early.

2. **Pipeline writes `body.get("content_hash") or ""` instead of `body`.** Confusing the dedup-hash plumbing with the raw payload. They're separate concerns; `raw_payload` gets the whole body dict, not a per-field extraction.

3. **Admin `readonly_fields` references `raw_payload` directly without excluding it.** Django sees both an explicit form field AND a readonly mention; you get a duplicated/confusing form. Exclude the actual field; reference only the pretty-print callable in `readonly_fields`.

4. **`json.dumps(..., default=str)` to "be safe."** Don't. The body is already JSON-native; `default=str` would silently mask a bug if a non-JSON value sneaks in.

5. **Forgetting `ensure_ascii=False`.** `json.dumps` escapes non-ASCII by default (`é` for `é`). Pass `ensure_ascii=False` so users see the actual characters in the admin.

6. **Forgetting `format_html` and concatenating strings.** Building HTML by string concatenation is an XSS vector. `format_html` escapes the JSON-text argument before substitution.

7. **Migration generated against a stale schema.** If the agent runs `makemigrations` while the model file is stale (e.g., from a prior aborted edit), the migration may pick up unrelated changes. Always: edit `models.py` first, then `makemigrations`. Verify the migration contains *only* the AddField op for `raw_payload`.

8. **Migration applied inside container but not committed.** The `web` container runs `migrate --noinput` on startup. The migration *file* must exist on host (committed) for `make rebuild` to bake it into the new image. Generating the migration inside the container creates it inside the container's filesystem, which is lost on rebuild.

9. **Test fixture pollution.** If tests upload bodies that share content with each other, the cross-doc_id dedup from Phase 7.5 will short-circuit. Each test should use unique content (e.g., parameterize `content` per test) OR use unique tenant/bot pairs. The existing `_body()` helper in test_pipeline.py uses fixed content; verify that test 1's body differs from test 2's content if both run in the same DB.

10. **Treating `raw_payload` as the source of truth for re-ingestion.** It's a debug aid, not a system of record. Don't write any logic that reads from `raw_payload` to drive uploads or chunking. Document this in the model's field help_text or docstring.

---

## Out of scope

- Auth (post-v1)
- Searching by `raw_payload` content (no GIN index)
- Backfilling old documents
- Truncating large payloads (storage is not a constraint per project memory)
- Audit log / version history (tracking changes to `raw_payload` over time — Phase 8 audit log if ever)
- Any change to the upload schema, the chunker, the embedder, the search algorithm, or the gRPC service
- Changes to `ScrapedItem`, `ScrapedSource`, or `build_payload` (unrelated to the Document model)
- Worker / Celery / async ingestion (post-v1)
- Per-tenant or per-bot retention policies on `raw_payload` (post-v1)

---

## Success looks like

- Operator opens `http://localhost:8080/admin/documents/document/`, clicks any document uploaded after Phase 7.6 ships, scrolls to "Raw payload (uploaded JSON)", and sees the exact JSON the scraper sent.
- For documents from before Phase 7.6, the same field shows `—`.
- Search, upload, delete, and gRPC behavior is byte-identical to Phase 7.5.
- `make run pytest -v` is fully green; CI is fully green.
