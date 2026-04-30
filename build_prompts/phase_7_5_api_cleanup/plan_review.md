# Phase 7.5 — Plan Review

> Adversarial review of `plan.md` (revision 1) per Prompt 2's 8 lenses. Severity tags: `[critical]` blocks ship, `[major]` likely defect, `[minor]` polish.

---

## Severity breakdown

- **[critical]:** 1
- **[major]:** 4
- **[minor]:** 5

## Findings escalated for revision

- F1 — confirm proto-field-number preservation in step 5.2 (already in plan; re-emphasized).
- F3 — add `embedder_available` fixture to test_search_http.py's `uploaded_doc` for parity with Phase 5/6.
- F4 — fixture cascade includes `test_delete.py` (transparent — slim fixture still has valid content; no assertions on dropped fields). Note in plan.
- F5 — clarify spec deliverables count: 11 vs 12 vs 14 modified depending on which list you read; plan goes with the deliverables-table count + inline pipeline.py + ambiguous test_search_query.py.

---

## Lens 1 — Spec compliance

### F1 [critical] — Proto field number preservation
Plan §3.1 spells out the diff. §5.2 verifies via `Chunk.DESCRIPTOR.fields`. **Resolution:** add an explicit additional assertion to step 5.2: confirm that `Chunk.DESCRIPTOR.fields_by_name['section_path'].number == 8`, `page_number == 9`, `score == 12`. Catches an accidental renumber that the field-name list alone wouldn't expose.

### F2 [major] — Spec deliverables internally inconsistent
Spec §"Deliverables" lists 11 modified + 1 new, but the bullet under `payload.py` says "explicitly modify `apps/ingestion/pipeline.py`" — making it 12 modified. Plan §7 already notes this. **Resolution:** plan goes with 12 modified (including pipeline.py) + 1 new (test_search_http.py) + 1 generated (implementation_report.md). No double-count risk.

### F3 [major] — `test_search_http.py` `uploaded_doc` fixture needs `embedder_available`
Plan §3.15 sketches the test file but the spec's snippet uses `uploaded_doc` directly without an embedder skip-graceful guard. On host, BGE-M3 cache permission blocks the upload (same as Phase 5/6 host-side). **Resolution:** add `embedder_available` fixture (same pattern as test_upload.py / test_delete.py) that the `uploaded_doc` fixture depends on. Tests that don't need an actual upload (validation, NOT_FOUND) skip it entirely.

### F4 [major] — Removed-field rejection covers all six dropped fields
Spec lists removed: `language` (top), `language` (item), `url` (item), `item_type` (item), `title` (item), `custom_metadata`. Plan §3.5's spec-quoted serializer code declares two sets:
- `REMOVED_FIELDS = {"language", "custom_metadata"}` (top-level)
- `REMOVED_ITEM_FIELDS = {"language", "url", "item_type", "title"}` (per-item)
Total: 6 fields covered. **Resolution:** verified; no plan change.

### F5 [minor] — HTTP search 503 Retry-After
Spec table maps Qdrant unavailable → 503 but doesn't mandate `Retry-After`. Phase 5b's 409 has it. v1 acceptable to omit. Plan §3.6 doesn't add a Retry-After header. **Resolution:** no change; flag as minor for Phase 8 hardening.

## Lens 2 — Edge cases

### F6 [minor] — `test_delete.py` reads the fixture
`grep -l valid_pdf_doc.json tests/` shows `test_upload.py` AND `test_delete.py`. `test_delete.py` uploads using the fixture body and then deletes. Slimming the fixture keeps the upload valid (still has `items[].content`); `test_delete.py` doesn't assert on dropped fields. **Resolution:** plan §3.9 verifies the fixture-as-uploaded path; test_delete.py runs unchanged. Document in plan §7 that `test_delete.py` is NOT modified but is a transparent consumer.

### F7 [minor] — `section_path=[]` roundtrip
Empty list → JSON `[]` → stored as `[]` in payload → search returns `[]`. Consistent. Plan §6 ambiguity A2 covers. No risk.

### F8 [minor] — HTTP cold load latency
First HTTP search after stack startup pays the BGE-M3 cold load tax (~30s in the web container's process if upload hasn't already warmed it). DRF view's request timeout is gunicorn's default 90s (per docker-compose.yml `--timeout 90`). Within budget. **Resolution:** plan §6 adds note A9.

### F9 [minor] — Backward-compat regression test
Spec hard constraint #3 says old chunks (uploaded with full payload) keep their fields and search returns them. Practical test would manually insert a Qdrant point with the old 20-field payload, then HTTP search → confirm response shape is valid. Plan §6 ambiguity A8 mentioned this as defensive. **Resolution:** plan deferred to Phase 8; not blocking.

## Lens 3 — Production readiness

### F10 [minor] — `make rebuild` vs `make up` after Phase 7.5 deploy
Plan §3.16 + R15 already note. Operator runbook (Phase 8) owns the explicit instruction. v1 acceptable.

## Lens 4 — Pitfall coverage audit (vs spec.md §"Common pitfalls")

| # | Spec pitfall | Plan addresses? |
|---|---|---|
| 1 | Renumbering proto fields | R1 [critical] + step 5.2 |
| 2 | Forgetting to regenerate stubs | step 3.2 + R9 |
| 3 | build_payload signature mismatch | R2 + step 3.4 (atomic) |
| 4 | Pipeline reads item_data["item_index"] | R5 + step 3.4 (auto-assign via enumerate) |
| 5 | Old chunks have full payload | R3 + spec hard constraint #3 |
| 6 | HTTP returns more keys than gRPC | R11 |
| 7 | source_type=text default test | step 3.12 (test_default_source_type_is_text) |
| 8 | valid_pdf_doc.json shared | R6 + F6 (test_delete.py noted) |
| 9 | empty-query whitespace | A6 |
| 10 | HTTP 404 vs gRPC NOT_FOUND | step 3.6 mapping; spec says same code "collection_not_found" |

All 10 covered.

## Lens 5 — Sequencing

Plan §2 is correct. The atomic step 3.4 (payload+pipeline together) is the key sequencing insight; emphasized in revision.

## Lens 6 — Verification commands

Plan §5 is comprehensive. Adding F1 resolution to 5.2.

## Lens 7 — Tooling correctness

`uvx --from grpcio-tools` (Phase 7's deviation) preserved in compile_proto.sh. Confirmed working in the Docker builder per Phase 7's report. No change.

## Lens 8 — Risk register completeness

R1-R15 plus F1-F10 covers the surface. No gaps.

---

## Recommendation

**Proceed with revised plan.** The 1 critical (F1) is preventive — already in the plan; revision adds one more assertion. The 4 majors fold into inline edits: F3 (embedder_available fixture), F4 (deliverable count clarified — no actual gap), F2/F6 (transparent fixture consumers).

Phase 7.5 is straightforward — no novel API research needed; build proceeds linearly per plan §2.

---

## End of review
