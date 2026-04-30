# Phase 2 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to BUILD what the revised plan describes, then VERIFY it against the spec, then REPORT honestly. Both halves matter.**

---

## Required reading (in this order)

1. `build_prompts/phase_2_domain_models/spec.md` — the source-of-truth specification. Re-read in full before you start.
2. `build_prompts/phase_2_domain_models/plan.md` — the revised plan from Step 2. Your roadmap.
3. `build_prompts/phase_2_domain_models/plan_review.md` — the critique. Read so you understand *why* the plan is shaped the way it is. Don't re-litigate decisions already made.
4. `build_prompts/phase_1_foundation/spec.md` — Phase 1's locked contract. Phase 2 must not regress any of it.
5. `build_prompts/phase_1_foundation/implementation_report.md` — what shipped in Phase 1.
6. `README.md` — for project context (skim).

If any of `spec.md`, `plan.md`, or `plan_review.md` for Phase 2 is missing, abort with a clear error message — Steps 1 and 2 must run first.

---

## Hard rules during implementation

1. **Follow the revised plan.** It exists for a reason. Deviations are allowed only when the plan is provably wrong; document each deviation in the final report.
2. **Build in the order the plan specifies.** Don't reorder for speed.
3. **Run the plan's verification commands at every checkpoint.** If a checkpoint fails, stop and fix before proceeding.
4. **Honor every "Out of scope" item.** Do NOT write DRF serializers, API views, Qdrant client code, embedder, chunker, gRPC, or audit log. Phase 2 ends where the plan ends.
5. **Do NOT modify Phase 1 files** (the 12 listed in spec.md hard constraint #1). Any edit there is a deviation that must be justified.
6. **No code comments unless the spec or a non-obvious invariant justifies them.**
7. **Never commit `.env`.** Phase 1 already gitignores it.
8. **`makemigrations` is run, and the auto-generated files are committed unmodified.** Do not hand-edit migrations.
9. **If `makemigrations` produces unexpected files (e.g. for an app you didn't touch), stop and inspect.** This usually means a model edit slipped into another app. Fix the actual cause; do not delete the spurious migration.
10. **No emoji in code or comments. No documentation files (`*.md`) beyond `implementation_report.md` unless the spec explicitly requires them.**

---

## Implementation phases

Execute the plan's build steps in order. The plan should match this rhythm.

### Phase A — Validators (dependency-free)

Create `apps/tenants/validators.py` with `SLUG_PATTERN`, `SLUG_REGEX`, `slug_validator`, `validate_slug`, and `InvalidIdentifierError`.

**Verify:**
```bash
uv run python -c "from apps.tenants.validators import slug_validator, validate_slug, InvalidIdentifierError, SLUG_PATTERN; print(SLUG_PATTERN); validate_slug('pizzapalace'); print('ok')"
```

### Phase B — Naming helpers (depends on validators)

Create `apps/qdrant_core/naming.py` with `collection_name(tenant_id, bot_id)` and `advisory_lock_key(tenant_id, bot_id, doc_id)`.

**Verify:**
```bash
uv run python -c "from apps.qdrant_core.naming import collection_name, advisory_lock_key; print(collection_name('pizzapalace', 'supportv1')); print(advisory_lock_key('pizzapalace', 'supportv1', 'doc-uuid-123'))"
```
Expected: `t_pizzapalace__b_supportv1` and a tuple of two ints.

### Phase C — Models

Replace stubs in `apps/tenants/models.py` (Tenant + Bot) and `apps/documents/models.py` (Document). Bot's `save()` does the function-local import of `collection_name` to avoid the import cycle.

**Verify:**
```bash
uv run python manage.py check
```
Should report 0 issues.

### Phase D — Admin

Replace `apps/tenants/admin.py` and `apps/documents/admin.py` with the registrations specified in `spec.md`.

**Verify:**
```bash
uv run python manage.py check
```

### Phase E — Migrations

Generate migrations:
```bash
uv run python manage.py makemigrations tenants documents
```
Two new files: `apps/tenants/migrations/0001_initial.py` and `apps/documents/migrations/0001_initial.py`. Inspect them — `tenants` should declare `Tenant` and `Bot`; `documents` should declare `Document` with a dependency on `tenants.0001_initial`.

**Verify:**
```bash
uv run python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

### Phase F — Apply migrations to test DB and confirm DDL

The `tests/test_settings.py` SQLite overlay is what pytest uses. To verify the schema is applied correctly there:
```bash
uv run python manage.py migrate          # uses tests.test_settings → in-memory SQLite for shell? NO — DJANGO_SETTINGS_MODULE for management commands is overridden. Use config.settings via env or invoke from inside the Compose web container.
```
Better path: apply against the running Compose Postgres:
```bash
docker compose -f docker-compose.yml exec web python manage.py migrate
```
Verify tables exist:
```bash
docker compose -f docker-compose.yml exec postgres psql -U aarav -d qdrant_rag -c "\dt"
```
Expected: `tenants_tenant`, `tenants_bot`, `documents_document` listed.

### Phase G — Tests

Create `tests/test_models.py` and `tests/test_naming.py` per the spec.

**Verify each independently:**
```bash
uv run pytest tests/test_naming.py -v
uv run pytest tests/test_models.py -v
```
Both must be green.

**Then run the full suite:**
```bash
uv run pytest -v
```
Phase 1's `test_healthz.py` must still pass alongside the new tests.

### Phase H — Stack-level smoke

```bash
docker compose -f docker-compose.yml down
docker compose -f docker-compose.yml up -d --build
sleep 60
docker compose -f docker-compose.yml ps                      # all healthy
curl -fsS http://localhost:8080/healthz | python -m json.tool
docker compose -f docker-compose.yml exec web python manage.py migrate --check  # 0 pending
docker compose -f docker-compose.yml exec web python manage.py createsuperuser --noinput --username admin --email admin@local || true
# (For interactive setup, omit --noinput.)
```

Verify admin login works:
```bash
curl -fsS http://localhost:8080/admin/login/ | grep -c "Django administration"
```
Expected: `1` or higher (the page contains the title).

Manually (in a browser) confirm:
- Log in to `/admin/`
- Create a Tenant with `tenant_id = "test_tenant"`
- Create a Bot under it with `bot_id = "test_bot"`
- Bot's `collection_name` field shows `t_test_tenant__b_test_bot` and is read-only
- Create a Document under the Bot

---

## Self-review

After Phase H passes, **stop building**. Run this self-review against the **spec**, not the plan.

For each acceptance criterion in `spec.md` (all 10), record:
- **Pass / fail** (be honest — partial passes count as fails)
- **Verification command run** (paste it)
- **Output observed** (paste relevant lines, redacting secrets)

For each pitfall in `spec.md` (all 10), record:
- **Avoided / hit / not-applicable**
- **How you confirmed**

For each "Out of scope" item, confirm not implemented.

---

## Final report

Save to `build_prompts/phase_2_domain_models/implementation_report.md`. Structure:

```markdown
# Phase 2 — Implementation Report

## Status
**OVERALL:** PASS / FAIL / PARTIAL

## Summary
- Files created: <N>
- Files replaced: <N>
- Files modified outside Phase 2 scope: <N> (must be 0)
- Migrations generated: 2 (paths)
- Tests added: <N>
- Tests passing: <N>/<N>
- Acceptance criteria passing: <N>/10

## Acceptance criteria (verbatim from spec.md)
### Criterion 1: <copy from spec>
- Result: PASS / FAIL
- Command: `<exact>`
- Output:
  ```
  <relevant>
  ```
- Notes: <caveats>
[... repeat for all 10]

## Pitfall avoidance (verbatim from spec.md)
### Pitfall 1: <copy from spec>
- Status: Avoided / Hit / N/A
- How confirmed: <command or reasoning>

## Out-of-scope confirmation
[brief list with "confirmed not implemented"]

## Phase 1 regression check
- All Phase 1 acceptance criteria still pass
- /healthz still returns green JSON on port 8080
- pytest of test_healthz.py still passes
- No Phase 1 source file modified (paste `git diff --name-only` output to prove it)

## Deviations from plan
[for each deviation: what · why · impact]

## Spec defects discovered
[anything in spec.md that turned out to be incorrect, contradictory, or impossible]

## Outstanding issues
[non-blocking but worth knowing before Phase 3]

## Files created or replaced
[clean tree from `find apps/tenants apps/documents apps/qdrant_core tests -type f -name "*.py" -newer build_prompts/phase_1_foundation/spec.md | sort`]

## Generated migrations
```
apps/tenants/migrations/0001_initial.py
apps/documents/migrations/0001_initial.py
```
(Paste the relevant CreateModel operations from each.)

## Commands to verify the build (one block, copy-pasteable)

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.yml down
docker compose -f docker-compose.yml up -d --build
sleep 60
docker compose -f docker-compose.yml exec web python manage.py migrate
curl -fsS http://localhost:8080/healthz | python -m json.tool
curl -fsS http://localhost:8080/admin/login/ | grep -c "Django administration"
```

## Verdict
One paragraph: is Phase 2 ready to ship? What's the user's next step (proceed to Phase 3, fix outstanding issues, escalate spec defects)?
```

---

## What "done" looks like for this prompt

When finished, output to chat:

1. Path to `implementation_report.md`.
2. **Overall status: PASS / FAIL / PARTIAL.**
3. Acceptance criteria score: `X/10 passed`.
4. Phase 1 regression check status (PASS / FAIL).
5. Recommended next step.

Then **stop**.

---

## A note on honesty

If something is broken, say so. A failing build reported as failing is more useful than a passing build that quietly skipped half the verification. The report is the contract — write it to be true, not flattering.
