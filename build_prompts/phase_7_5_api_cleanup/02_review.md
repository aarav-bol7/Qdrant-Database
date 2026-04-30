# Phase 7.5 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_7_5_api_cleanup/spec.md` — source of truth.
2. `build_prompts/phase_7_5_api_cleanup/plan.md` — to critique.
3. `build_prompts/phase_7_search_grpc/spec.md` — Phase 7's gRPC contract.
4. `build_prompts/phase_5a_upload_core/spec.md` — Phase 5a's upload contract.
5. `build_prompts/phase_5b_upload_idempotency/spec.md` — Phase 5b's idempotency layer.
6. `build_prompts/phase_4_embedding_chunking/spec.md` — Phase 4 contract.
7. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
8. `README.md` — context.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_7_5_api_cleanup/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_7_5_api_cleanup/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 12 modified/new files addressed?
- All 11 hard constraints addressed (especially proto `reserved`, `source_type` default, `item_index` auto-assign, removed-field rejection)?
- All 11 acceptance criteria mapped to steps?
- All 10 common pitfalls in risk register?
- Out-of-scope respected?

### Lens 2 — Edge cases the plan missed

- **Proto field renumbering trap.** Spec says use `reserved` for removed numbers. Plan must explicitly verify the agent's edit to `search.proto` includes `reserved 7, 10, 11; reserved "section_title", "category", "tags";` AND does NOT renumber the remaining fields.
- **`uv run` vs direct `python` for `compile_proto.sh`.** The script uses `uv run python -m grpc_tools.protoc`. From inside the Dockerfile RUN, is `uv` on PATH? Phase 7's Dockerfile runs the script during build — should still work. Plan should verify.
- **Generated stubs cache invalidation.** `compile_proto.sh` regenerates stubs into `apps/grpc_service/generated/`. The Dockerfile's RUN cache may invalidate based on `proto/search.proto` mtime — confirm.
- **`build_payload` signature change breaks pipeline.py.** Same commit must update both. Plan should verify the order and the test at the boundary (`manage.py check` after both edits).
- **`UploadItemSerializer` accepting `item_index` but ignoring it.** Plan should pick: (a) add `item_index = serializers.IntegerField(required=False)` and document it's discarded by the pipeline; OR (b) reject `item_index` in body with a friendly 400 message. Plan should commit one way.
- **Removed-field rejection check on `self.initial_data`.** When DRF parses JSON, `self.initial_data` is the raw input dict. With form data it'd be a QueryDict. Phase 5/7 are JSON-only; verify the check works for the JSON path.
- **`source_type=text` chunker behavior.** New CHUNK_CONFIG entry: `"text": {"size": 400, "overlap_pct": 0.10}`. Same as DEFAULT_CHUNK_CONFIG. So existing logic that falls back to DEFAULT for unknown source_type would behave the same way for `text`. Why have both `text` AND DEFAULT? Plan should justify (answer: explicit > implicit; "text" is a valid known type, not an unknown one).
- **HTTP search response has UNICODE.** `text` may contain non-ASCII chars (Cyrillic, CJK, emojis). DRF's JSON renderer handles this; just confirm the test fixture exercises non-ASCII content (or skip — Phase 7 already covered this).
- **`top_k=0` in HTTP request.** DRF's `IntegerField(min_value=1)` rejects with 400 (validation error). Spec says 400. Verify.
- **Filter `source_types` with empty list.** `[]` is falsy; the view passes `None` to `search()`. Empty list should NOT mean "no source_type matches" — it means "no filter on source_type". Plan should verify.
- **`section_path=[]` after JSON round-trip.** Empty list serializes as `[]` (not `null`). Stored as `[]` in payload. HTTP returns `[]`. Consistent. ✓.
- **gRPC client cache after Chunk message change.** External clients (DynamicADK?) using the old `search.proto` would deserialize `section_title`/`category`/`tags` from old chunks but fail to deserialize new chunks (they expect those fields and get nothing). Proto3 unknown-field handling: deserializes successfully with default values for missing fields. Plan should note this.
- **Search behaves identically for old chunks.** Test should upload with the OLD schema (manually constructing a Document row + Qdrant chunks with all 20 payload fields), then search → response should still be valid. Hard to construct in a test; could mock or use a manually-crafted Qdrant point.

### Lens 3 — Production-readiness gaps

- **HTTP search route ordering.** `urls.py` adds the new route at the end of the urlpatterns list. Django matches first-match — confirm no earlier pattern accidentally swallows the new path.
- **HTTP search response timing.** Same as gRPC: ~50–150ms warm, ~30s cold. Plan should mention the warmup recommendation transitively from Phase 7.
- **HTTP search rate limit.** None in v1. Phase 8 adds metrics; rate limit is post-v1.
- **SearchFiltersSerializer `only_active=True` default.** If the client sends `{"query": "x"}` without `filters`, the view passes `{}` filters → `only_active` defaults to True via the serializer. Verify the `not filters.get("only_active", True)` check is INSIDE the validate() method (not at the view level).
- **HTTP search 503 response includes `Retry-After`?** Spec doesn't mandate. Phase 5b's 409 has it. v1 acceptable to omit on 503.
- **Removed-field rejection covers all dropped fields.** Verify `language`, `custom_metadata`, `items[].language`, `items[].url`, `items[].item_type`, `items[].title` are all in the REMOVED set.

### Lens 4 — Pitfall coverage audit

For all 10 spec.md pitfalls.

### Lens 5 — Sequencing & dependency correctness

Critical sequence:
- proto first → regen stubs.
- chunker.py "text" entry early (no deps).
- payload.py signature change SAME commit as pipeline.py call-site update.
- serializers.py edits: slim upload + add search; same commit.
- views.py adds search view (depends on serializer + search.search()).
- urls.py adds route (depends on view).
- handler.py edits (independent of serializer changes).
- Test fixture before tests.
- Tests last.
- Stack rebuild after all source changes.

### Lens 6 — Verification command quality

- After proto regen: import the regenerated `Chunk` and inspect its fields (`Chunk.DESCRIPTOR.fields`) — strong (catches accidental renumbering).
- After payload.py: `inspect.signature(build_payload)` shows no `custom_metadata` kwarg — strong.
- After pipeline.py: `manage.py check` runs imports and confirms no broken references — strong.
- Manual curl smoke covers minimum-body upload + HTTP search + removed-field rejection — strong.
- pytest test_search_http.py covers all error codes — strong.
- Phase 1-7 regression via full suite + `make health` — strong.

### Lens 7 — Tooling correctness

- `bash scripts/compile_proto.sh` requires `grpc_tools.protoc`. Phase 7 added `uvx --from grpcio-tools` invocation. Verify the script still works post-Phase-7's deviation.
- `python -m json.tool tests/fixtures/valid_pdf_doc.json` for fixture validation — standard.
- DRF `IntegerField(min_value=..., max_value=...)` for top_k — standard.

### Lens 8 — Risk register completeness

- **Image cache:** Dockerfile's `RUN bash scripts/compile_proto.sh` cache invalidates when `proto/search.proto` changes. Phase 7's Dockerfile placement is correct. Plan should verify it's still in the right spot.
- **Phase 7's existing tests** call `chunk.section_title` etc. — those tests must update or break. Plan should grep for `section_title` / `category` / `tags` in the existing test files and inventory.
- **Old gRPC clients** using stale `search_pb2` — break? No: proto3 unknown-field handling allows wire-compat for additions/removals as long as field numbers don't reuse. The `reserved` directive enforces this.

---

## Output structure

### File 1: `plan_review.md` (NEW)

Standard structure with sections per lens, summary, recommendation.

### File 2: `plan.md` (OVERWRITE)

Same structure as the original. Add section 0: **"Revision notes"** linking to plan_review.md finding numbers. Resolve all [critical] and [major] findings inline.

---

## What "done" looks like

Output to chat:

1. Both files saved.
2. Severity breakdown.
3. Findings escalated.
4. Recommendation.

Then **stop**.
