# Phase 7.6 — Plan Review

> Adversarial review of `plan.md` (revision 1). Severity tags: `[critical]` blocks ship, `[major]` likely defect, `[minor]` polish.

---

## Severity breakdown

- **[critical]:** 0
- **[major]:** 3
- **[minor]:** 5

## Findings escalated for revision

- F1 — clarify the migration auto-name + idempotent re-run behavior (Django sometimes picks a different filename than the spec; spec line 91 said `0002_document_raw_payload.py` but the auto-namer might choose differently).
- F2 — admin pretty: spec wants `obj.raw_payload is None` → `"—"`, but Prompt 3 sample uses `if not obj.raw_payload` (also catches `{}` and `[]`). Pick one and document.
- F4 — confirm Prompt 3's test slugs (`test_t`, `test_b`) don't collide with existing tests in same DB.

---

## Lens 1 — Spec compliance

### F1 [major] — Migration filename auto-naming
Spec line 61 / hard constraint #2 mandates the file `apps/documents/migrations/0002_document_raw_payload.py`. Django's auto-namer for `AddField` typically produces `0002_document_raw_payload.py` when the model has only one new field — matches. **Plan §3.2 already accepts the auto-generated name** (and §6 A3 documents that if Django picks a different name like `0002_alter_*`, accept and document). No further plan change.

### F2 [major] — Admin: `is None` vs falsy check for `—` placeholder
Spec hard constraint behavior table says `"—"` if `obj.raw_payload is None`. Prompt 3's sample code uses `if not obj.raw_payload` (also returns `—` for `{}` or `[]`). These differ:
- Empty body `{}` would be impossible at the pipeline (UploadBodySerializer requires non-empty `items`), so the practical cases are: `None` (legacy/failed) OR a populated dict (success).
- `if not obj.raw_payload` is the safer default — empty dicts would never reach the admin path in practice but the broader check is harmless.

**Resolution:** plan §3.5 uses `if obj.raw_payload is None: return "—"` (matches spec literal). Prompt 3's sample is also fine semantically; plan flags the divergence for the implementer to pick the spec-literal form unless empty-dict appearance is observed.

### F3 [major] — `_body()` helper still includes `content_hash`
The existing `_body()` in `tests/test_pipeline.py` (Phase 7.5 form) builds `{"source_type": "pdf", "source_filename": "x.pdf", "content_hash": "sha256:abc", "items": [...]}` by default. Plan §3.6 reuses `_body()` and overrides `content_hash` per-test for unique hashes. Confirmed compatible.

### F4 [minor] — Test slug uniqueness
Plan §3.6 chose `(rp_a, rp_b)`, `(rp_a2, rp_b2)`, `(rp_a3, rp_b3)`. Prompt 3's sample uses `(test_t, test_b)` for all three. Both work because of pytest-django's per-test transaction rollback; plan's unique slugs are extra-defensive but not required.

**Resolution:** plan keeps unique slugs. Prompt 3's literal `test_t`/`test_b` would also work due to transaction rollback; either is acceptable.

## Lens 2 — Edge cases

### F5 [minor] — `body` dict identity vs deep equality after JSONField round-trip
After `update_or_create(defaults={"raw_payload": body})` and a re-fetch, `Document.raw_payload` is a fresh dict deserialized from Postgres jsonb (via Django's JSONField). `==` works (deep equality) but `is` does NOT. Plan §3.6 uses `==` everywhere; correct.

### F6 [minor] — Cyrillic / CJK / emoji content
`json.dumps(..., ensure_ascii=False)` preserves multi-byte chars; `format_html` HTML-escapes them safely. Plan §3.5 covers both. No defect.

### F7 [minor] — `raw_payload` size vs Postgres jsonb
Plan §R8 documents the ~100 MB practical ceiling. Per project memory ("storage is not a concern"), v1 accepts. No plan change.

## Lens 3 — Production readiness

### F8 [minor] — No GIN index on `raw_payload`
Spec out-of-scope. Sequential scan on admin search is fine for small admin user base. v1 accepted.

### F9 [minor] — `raw_payload` not in API responses
Plan §R11 confirms no leak path. No view returns it. Admin-only exposure.

## Lens 4 — Pitfall coverage audit

| # | Spec pitfall | Plan addresses? |
|---|---|---|
| 1 | Pipeline writes `raw_payload` on no_change | R1 + step 3.4 explicit + test 2 |
| 2 | Confused with content_hash | step 3.4 writes whole `body` dict |
| 3 | Admin double-renders `raw_payload` | R4 + step 3.5 `exclude` + readonly callable |
| 4 | `default=str` masking bugs | plan §3.5 uses `json.dumps(obj.raw_payload, indent=2, ensure_ascii=False)` only — no `default=` |
| 5 | Forgetting `ensure_ascii=False` | step 3.5 explicit |
| 6 | Forgetting `format_html` | step 3.5 explicit |
| 7 | Migration captures unrelated drift | R3 + step 3.3 inspection |
| 8 | Migration generated inside container | R2 + step 3.2 uses `make makemigrations-host` |
| 9 | Test fixture pollution from cross-doc_id dedup | R6 + step 3.6 unique slugs/hashes |
| 10 | Reading `raw_payload` for re-ingestion | model help_text in step 3.1 documents debug-only |

All 10 covered.

## Lens 5 — Sequencing

Plan §2 order: models.py → makemigrations-host → inspect → pipeline.py → admin.py → tests → rebuild → smoke → reversibility → regression. Correct. Steps 4 (pipeline.py) and 5 (admin.py) are independent — parallelizable; step 6 (tests) depends on both.

## Lens 6 — Verification command quality

| Step | Strength |
|---|---|
| 5.1 model introspection | strong (catches typos in field params) |
| 5.3 migration body inspection | strong (catches drift) |
| 5.5 grep `raw_payload` count | strong (catches accidental write on no_change branches) |
| 5.6 admin attrs introspection | strong |
| 5.7 pipeline tests | strong (covers create/no_change/replace) |
| 5.9 manual admin smoke | strong (visual proof) |
| 5.10 reversibility | strong (catches non-default reverse logic) |

## Lens 7 — Tooling correctness

`make makemigrations-host APP=documents` uses the SQLite test_settings overlay (no DB connection). Verified compatible with Django's makemigrations.

`make run pytest` invokes pytest inside the web container (real Postgres). For test_pipeline.py with mocked embedder + autouse upload_lock noop fixture, host run also works.

`make rebuild` triggers a full Docker image rebuild (preserves volumes). After rebuild, `migrate --noinput` in the web container CMD applies 0002 against production Postgres.

## Lens 8 — Risk register completeness

### F10 [minor] — Phase 7.5 tests don't reference `raw_payload`
Confirmed via plan §7's don't-touch list. None of `test_upload`, `test_search_*`, `test_payload`, `test_chunker`, `test_embedder`, `test_locks`, `test_delete` import or assert on `raw_payload`. Phase 7.6's tests are additive.

### F11 [minor] — Phase 6 delete tests
`tests/test_delete.py` reads the fixture (transparent consumer per Phase 7.5's plan_review F6). Doesn't reference `raw_payload`. No regression risk.

### F12 [minor] — Migration dependency chain
Plan §3.3 + §6 A3 verify parent is `('documents', '0001_initial')`. Django's auto-dep-detection picks the latest existing migration as parent. Confirmed.

---

## Recommendation

**Proceed with revised plan.** Zero critical findings. Three majors (F1/F2/F3) are clarifications rather than rework — F1 (migration auto-name) already covered by plan §6 A3; F2 (admin null check) keep spec-literal `is None`; F3 (test helper compat) confirmed compatible. Five minors are documentation polish.

The plan covers all 12 hard constraints, all 11 acceptance criteria, all 10 pitfalls. No gaps.

---

## End of review
