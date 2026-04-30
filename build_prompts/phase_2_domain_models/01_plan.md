# Phase 2 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to PLAN, not to write code. Do not create source files. Do not run `manage.py makemigrations`. Do not modify any Phase 1 file.**

---

## Required reading (in this order)

1. `README.md` — project charter; understand the 8-phase roadmap and where Phase 2 fits.
2. `build_prompts/phase_2_domain_models/spec.md` — the full Phase 2 specification with deliverables, hard constraints, acceptance criteria, and pitfalls. **Source of truth. Read it twice.**
3. `build_prompts/phase_1_foundation/spec.md` — the Phase 1 contract; understand what is locked and must NOT be modified.
4. `build_prompts/phase_1_foundation/implementation_report.md` — what shipped in Phase 1 (do not regress any of it).
5. `rag_system_guide.md` if present — design rationale for the metadata layer (skim §5).

If `phase_2_domain_models/spec.md` does not exist, abort with a clear error message.

---

## Your task

Produce a structured implementation plan for Phase 2. Save it to:

```
build_prompts/phase_2_domain_models/plan.md
```

The plan is a working document — Step 2 (`02_review.md`) will critique it; Step 3 (`03_implement.md`) will execute it. Quality of this plan determines the quality of the build.

---

## What the plan must contain

Use this exact section structure, in this order:

### 1. Plan summary

3–5 sentence executive summary at the top (write last). What's getting built? What's the riskiest part? How will the build verify itself?

### 2. Build order & dependency graph

Enumerate every file from spec.md's "Deliverables" tree (9 entries). For each: path · what Phase 2 needs it to do · what it depends on (other files / Phase 1 components) · which build step creates it. Plus the auto-generated migration files.

### 3. Build steps (sequenced)

A numbered list of 8–14 build steps, in execution order. Each step:
- **Goal** (one sentence)
- **Files touched** (paths)
- **Verification command** (the strongest cheap check that confirms this step)
- **Rollback action** (what to undo if this step fails — usually `git checkout -- <file>` or removal)

The first steps must be the dependency-free foundation: `apps/tenants/validators.py` (no Django model imports), then `apps/qdrant_core/naming.py` (depends on validators only). Then models, then admin, then `makemigrations`, then `migrate`, then tests, then full-stack smoke.

### 4. Risk register

For each plausible failure mode, list: risk · likelihood · impact · mitigation · detection. Cover at minimum:

- **Circular import** between `apps.tenants.models`, `apps.tenants.validators`, and `apps.qdrant_core.naming`
- **`Bot.save()` not populating `collection_name`** because `super().save()` is called before computing
- **`@pytest.mark.django_db` missing on model tests** — tests fail with database access errors
- **Migration generated for unintended apps** — `makemigrations` picks up other apps' models inadvertently
- **`makemigrations` produces two migrations per app instead of one** — model edited mid-generation
- **The codebase-grep test in `test_naming.py` flags a false positive** (e.g. matching content in `build_prompts/` or in a docstring)
- **`unique=True` on `Bot.collection_name` collides with existing rows** in a re-run scenario (irrelevant on a fresh DB but worth noting)
- **Cascade-delete misconfiguration** — `on_delete` field forgotten somewhere
- **`__str__` methods crash on partially-constructed instances** during admin display
- **Phase 1 file accidentally modified** by an editor auto-save / format-on-save touching adjacent files

Plus any others you identify.

### 5. Verification checkpoints

Pause-and-verify points during the build. At each: exact commands and expected outcome. Aim for 6–10 checkpoints:

- After validators.py: `uv run python -c "from apps.tenants.validators import slug_validator, validate_slug, InvalidIdentifierError; print('ok')"`
- After naming.py: `uv run python -c "from apps.qdrant_core.naming import collection_name, advisory_lock_key; print(collection_name('pizzapalace', 'supportv1'))"`
- After model files: `uv run python manage.py check`
- After `makemigrations`: confirm two new files; inspect them
- After `migrate` against the running stack: verify tables exist
- After tests: `uv run pytest -v`
- After full-stack rebuild: healthz still green; admin login works; create-tenant-then-bot via shell or admin succeeds

### 6. Spec ambiguities & open questions

Read the spec critically. Wherever it underspecifies something, list:
- **What's ambiguous**
- **Your proposed interpretation** (don't ask the user — make a call)
- **Reversibility** (how hard to fix later if the call is wrong)

Things to scrutinize:
- The spec's `Bot.save()` example uses a function-local import of `collection_name`. Is this the right cycle-breaker, or should naming.py import models lazily instead?
- The grep test in `test_naming.py` searches `apps/` and `config/`. Should it also cover `scripts/`? `tests/` itself is excluded — what about `proto/` (currently empty)?
- `Document.tenant_id` and `Document.bot_id` are denormalized but the spec says Phase 2 doesn't enforce that they match `bot.tenant_id`/`bot.bot_id`. Should Phase 2 add a `clean()` method that asserts this, or trust Phase 5? (Spec says trust Phase 5; confirm this is the right call.)
- `Bot.collection_name`'s `unique=True` adds a UNIQUE INDEX. Is the implicit B-tree index on the underlying CharField sufficient, or should we add an explicit `db_index=True`?
- The auto-generated migration filenames depend on Django's slug logic. The spec accepts whatever names Django produces. Does this need to be more specific?
- `tests/test_naming.py`'s grep uses `subprocess.run(["grep", ...])`. This pins the test to systems with `grep` on PATH. Is `re` over the file contents a more portable alternative?

Aim for 5–10 entries.

### 7. Files deliberately NOT created / NOT modified

- Echo the spec's "Out of scope" list in your own words.
- Add the explicit Phase 1 don't-touch list (the 12 files in spec.md hard constraint #1).
- Note that `apps/ingestion/`, `apps/grpc_service/`, and the `core` app stay untouched in Phase 2.

### 8. Acceptance-criteria mapping

For each of the 10 acceptance criteria in spec.md: criterion summary · which build step satisfies it · verification command · expected output. Every criterion must map to at least one step.

### 9. Tooling commands cheat-sheet

The exact commands you'll use. At minimum:

```
uv run python manage.py makemigrations tenants documents
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py migrate          # against host SQLite (test_settings) or live Postgres
uv run python manage.py shell             # to manually create/inspect rows
uv run python manage.py createsuperuser   # for admin login
uv run pytest tests/test_models.py -v
uv run pytest tests/test_naming.py -v
uv run pytest                             # full suite
uv run ruff check .
uv run ruff format --check .
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml exec web python manage.py migrate
docker compose -f docker-compose.yml exec web python manage.py createsuperuser
```

Note non-obvious choices (e.g. why scope `makemigrations` to just `tenants documents` instead of running it bare).

### 10. Estimated effort

A rough wall-clock estimate per build step. Surface places where a step will be much longer than its neighbors (a sign of hidden complexity).

---

## Output format

A single markdown file at `build_prompts/phase_2_domain_models/plan.md`. Use clear `##` and `###` headings, tables where they help, code blocks for commands. Aim for 350–650 lines.

---

## What "done" looks like for this prompt

When finished, output to chat:

1. Confirmation that `plan.md` was created.
2. Total line count of the plan.
3. A 5-bullet summary of the plan's key sequencing decisions.
4. Any spec ambiguities flagged in section 6 (just the titles).

Then **stop**. Do NOT start implementing. Step 2 (`02_review.md`) reviews this plan before any code is written.
