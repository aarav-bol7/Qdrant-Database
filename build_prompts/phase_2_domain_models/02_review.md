# Phase 2 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to CRITIQUE the plan from Step 1 and then revise it. Do not write production code. Do not run `makemigrations` or modify Django models. The only file you create is the revised plan plus a critique document.**

---

## Required reading (in this order)

1. `build_prompts/phase_2_domain_models/spec.md` — the source-of-truth specification. Read first.
2. `build_prompts/phase_2_domain_models/plan.md` — the plan from Step 1. Read after the spec, with the spec fresh in your head, so you can compare.
3. `build_prompts/phase_1_foundation/spec.md` — Phase 1's locked contract. The Phase 2 plan must respect Phase 1's hard constraints.
4. `README.md` — for project context (skim).

If `plan.md` does not exist, abort with: `"Plan not found. Run PROMPT 1 first."`

---

## Your task

Adversarially review `plan.md`. Find every gap, wrong assumption, missed edge case, and production-readiness flaw. Then produce a **revised plan** that addresses every finding.

Save outputs to:

- `build_prompts/phase_2_domain_models/plan_review.md` — the critique findings (NEW file)
- `build_prompts/phase_2_domain_models/plan.md` — **overwritten** with the revised plan

Keep the original plan's structure (sections 1–10) — your revision adds, corrects, or extends. Don't restructure for the sake of it.

---

## Review lenses

Apply each lens systematically. For each lens, list findings even if you find none (write `"no findings"` so the user trusts you actually checked). Tag each finding with severity: **[critical]** (blocks the build), **[major]** (will cause a re-do), **[minor]** (polish).

### Lens 1 — Spec compliance

For each requirement in `phase_2_domain_models/spec.md`, verify the plan addresses it:

- Every file in the deliverables tree (9 entries)
- Every hard constraint (the 11 in spec.md's "Hard constraints" section)
- Every locked stack version (unchanged from Phase 1 — should not be re-pinned)
- Every acceptance criterion (all 10)
- Every common pitfall (all 10)
- The "Out of scope" list — does the plan respect it?

Flag any requirement the plan misses or handles incorrectly.

### Lens 2 — Edge cases the plan missed

For each step in the plan, ask "what could happen here that the plan didn't anticipate?" At minimum check:

- **Circular imports.** The validators-models-naming chain has a known cycle risk. Does the plan break it correctly? Is `Bot.save()` doing function-local import or module-top? Is `naming.py` importing from `tenants.models` anywhere (it must NOT)?
- **`makemigrations` running against the host's DB vs the Compose DB.** Where is `makemigrations` run — host (uses `tests.test_settings` SQLite overlay since `pyproject.toml` sets DJANGO_SETTINGS_MODULE) or Compose web container (uses `config.settings` against the real Postgres)? The migration content shouldn't differ, but the test DB and prod DB get migrations applied at different times. Does the plan address both?
- **Migration ordering.** `documents/0001_initial.py` depends on `tenants.0001_initial`. Does makemigrations correctly pick this up? Does the plan verify by inspecting the dependencies declared in the generated file?
- **Test-DB migration application.** pytest-django runs migrations on the SQLite test DB before tests. If migrations have postgres-specific operations (e.g. CITEXT, advisory locks DDL), they fail on SQLite. Does the plan verify the migrations are SQLite-compatible?
- **`Bot.save()` with `force_insert` or `force_update`.** Django's save() accepts these kwargs. Does the override forward them correctly via `*args, **kwargs`?
- **Re-saves of an existing Bot with a different bot_id or tenant.** If `bot.bot_id` is changed and saved, `collection_name` is recomputed. Is this desirable? Probably yes (collection_name follows the slugs). The plan should explicitly handle this.
- **`Document` without an explicit `bot_id`/`tenant_id` set.** If a caller does `Document.objects.create(bot=b, source_type="pdf", content_hash="...")`, the denormalized fields are blank/None. Does the spec accept this, or should the model populate them in `save()`? (Spec says trust Phase 5 — but does Phase 2 at least add a `__init__` or `save()` that defaults them from `bot` if not given?)
- **Admin fields readonly on creation vs edit.** Some admin fields like `collection_name` should be readonly always; some like `created_at` should appear on edit but not on creation. Does the plan distinguish?
- **DjangoAdmin `list_filter` on `('tenant',)`** — this works because Django auto-renders FK filters. But the spec also has Document `list_filter = ('status', 'source_type', 'tenant_id')` where `tenant_id` is a CharField (not FK). Does this work as expected, or does it need a special filter class?
- **Pytest-django DB destruction between tests.** Tests in this phase create rows. By default pytest-django wraps each test in a transaction and rolls back. Does the plan rely on this? If a test uses `@pytest.mark.django_db(transaction=True)` for some reason, behaviors change.
- **The grep guard test.** `subprocess.run(["grep", ...])` requires `grep` on PATH. Inside Docker (`python:3.13-slim`), `grep` is installed. On macOS it's BSD grep, not GNU — flag differences. On Windows it's missing. Does the plan flag this portability concern, or replace with a Python-level scan?

Add any other edge cases you think of.

### Lens 3 — Production-readiness gaps

Phase 2 must ship a foundation that's production-grade:

- **Index coverage.** Are the indexes on `Document(tenant_id, bot_id)` and `Document(status)` sufficient for the hot paths? What about indexes on `Bot(tenant, bot_id)` (already in spec) and `Tenant(name)` (search field, no index)? Should we add `db_index=True` selectively?
- **Migration reversibility.** Are the auto-generated migrations reversible (`migrate tenants zero`)? Phase 2 likely passes by default, but flag any data-bearing fields that block rollback.
- **Deletion safety.** `on_delete=CASCADE` is destructive. For Tenant deletion, this destroys an entire customer's data. Should Phase 2 add an additional gate (e.g. soft-delete with `deleted_at`)? Spec says no (defer to v3 audit log) — confirm the plan respects this, but flag the operational risk for the Verdict section.
- **`__str__` safety.** Models' `__str__` methods are called in admin lists and logs. If a Document's `source_filename` and `source_url` are both None, the `__str__` returns "—". Does the plan verify this in tests?
- **Test isolation.** Two tests creating tenant "pizzapalace" in the same DB transaction will conflict. Does the plan use unique IDs per test (e.g. `f"test_{uuid.uuid4().hex[:8]}"`) or rely on transaction rollback? If transaction rollback (default), it's fine; flag the assumption.
- **Database-level constraint violation handling.** Tests expecting `IntegrityError` use `transaction.atomic()` to scope the failure. Does the plan use this pattern correctly?
- **`null=True, blank=True` semantics.** Document.source_filename has both. CharField with `null=True` is generally a Django anti-pattern (use empty string instead); but for a column that's genuinely "nothing here" (URL doc has no filename), `null=True` is correct. Confirm the plan uses null/blank correctly.
- **`auto_now` updates last_refreshed_at on every save** — including for `status` changes and chunk_count updates. Phase 5's no-op short-circuit explicitly bumps last_refreshed_at; the existing `auto_now=True` already does this implicitly. Is there a conflict?
- **Logging of model events.** Django doesn't log creation by default. Should Phase 2 add `post_save` signals for structured logging? Spec says no audit log — confirm this is the right call.
- **JSON serializability of model fields.** Document.error_message is TextField (could be a stack trace). When dumped to logs, this could be huge. Does the plan flag this for Phase 5/8?

### Lens 4 — Pitfall coverage audit

The spec lists 10 pitfalls. For each, verify:

1. The plan explicitly addresses it (in a build step or risk register entry).
2. The verification commands would actually catch the pitfall if it occurred.

If pitfall #3 (`@pytest.mark.django_db` missing) is in the risk register but the plan doesn't have a verification step that runs the model tests and checks for "Database access not allowed" errors, that's a finding.

### Lens 5 — Sequencing & dependency correctness

Walk the plan's build steps in order. For each:

- Does it need anything from a later step? (circularity = plan is wrong)
- Could it be done earlier? (delayed step = over-sequencing)
- If interrupted after this step, is the working dir in a coherent state?

A correct plan has strict topological order. The dependency-free files (validators.py, naming.py — both pure-Python, no Django imports beyond `RegexValidator`) come first. Then models. Then admin (depends on models). Then migrations (depends on models). Then tests (depends on everything).

### Lens 6 — Verification command quality

For each verification command:

- Does it actually verify the goal of the step, or just confirm the command exited 0?
- Is it the cheapest reliable check?
- Does it produce useful output on failure?

Replace weak verifications with stronger ones. Examples:
- After `makemigrations`: don't just check exit code — open the file and confirm it has the expected `CreateModel` operations.
- After admin registration: don't just check `manage.py check` — actually load `/admin/` and verify the model lists appear.
- For the grep test: `subprocess.run(["grep", ...])` should be replaced with pure-Python file scan to remove the grep dependency (or the test file should be in `tests/` only and the test should be skip-conditional if grep is missing).

### Lens 7 — Tooling correctness

Spot-check the cheat-sheet:

- Are flags up-to-date with current `uv` and `manage.py`?
- `python manage.py makemigrations tenants documents` vs bare `python manage.py makemigrations` — which is preferred? The bare form picks up all installed apps; the scoped form is safer.
- `migrate` against the dev override Postgres works only when the stack is up. Does the plan account for this, or does it run `migrate` against the host SQLite test DB?
- `createsuperuser` is interactive by default. Does the plan flag this, or use `DJANGO_SUPERUSER_*` env vars + `--noinput`?

### Lens 8 — Risk register completeness

Risks the plan may have missed:

- The grep test failing on a clean checkout because a Phase 2 file does have a `t_..._b_` literal in a docstring or test fixture
- Auto-generated migration files containing model field options that don't match the model — possible if `makemigrations` runs while a model is being edited
- The `web` container's `migrate` step running before code that references the new tables (e.g. healthz starts pinging tables that don't exist yet) — actually irrelevant for Phase 2 because healthz doesn't query any models, but worth flagging as a future concern
- Admin Login redirect after successful auth — depends on `LOGIN_REDIRECT_URL` setting; default should work
- Test runtime grew by N seconds because every model test rolls back a transaction — minor, just worth knowing

---

## Output structure

### File 1: `plan_review.md` (NEW)

```markdown
# Phase 2 Plan Review

## Summary
- Total findings: <N>
- Severity breakdown: <X critical, Y major, Z minor>
- Plan accuracy: <%> spec compliance
- Recommendation: accept revised plan / re-plan / escalate to user

## Findings by lens

### Lens 1 — Spec compliance
1. **[severity] Title.** <description>. Where in plan: <section/line>. How to fix: <action>.

### Lens 2 — Missed edge cases
...

### Lens 3 — Production-readiness gaps
...

### Lens 4 — Pitfall coverage audit
...

### Lens 5 — Sequencing & dependency correctness
...

### Lens 6 — Verification command quality
...

### Lens 7 — Tooling correctness
...

### Lens 8 — Risk register completeness
...

## Findings escalated to user
<entries that need user decision before Prompt 3 can run>
```

Severity tags: **[critical]** (blocks the build), **[major]** (will cause a re-do), **[minor]** (polish).

### File 2: `plan.md` (OVERWRITE)

Same 10-section structure as the original. Add a new section 0 at the top: **"Revision notes"** — list what changed vs the previous version, with cross-references to `plan_review.md` finding numbers. Resolve all [critical] and [major] findings inline. [minor] can be batched into one risk-register entry or accepted as-is — note your call.

---

## What "done" looks like for this prompt

When finished, output to chat:

1. Confirmation that both files were saved.
2. Severity breakdown of findings.
3. Any findings escalated to the user (titles only).
4. Recommendation: ready for Prompt 3, or does the user need to weigh in?

Then **stop**. Do NOT begin implementation.
