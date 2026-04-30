# Phase 1 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to PLAN, not to write code. Do not create any source files. Do not run `uv init`. Do not touch `pyproject.toml`. Do not start docker.**

---

## Required reading (in this order)

1. `README.md` — project charter; understand what qdrant_rag is and the 8-phase roadmap.
2. `build_prompts/phase_1_foundation/spec.md` — the full Phase 1 specification with deliverables, constraints, acceptance criteria, and pitfalls. **This is your source of truth for what to build. Read it twice.**
3. `rag_system_guide.md` — design rationale (skim only — Phase 1 doesn't implement RAG features).

If `rag_system_guide.md` is not present in the working directory, note it in your output and continue — the spec is sufficient for Phase 1.

---

## Your task

Produce a structured implementation plan for Phase 1. Save it to:

```
build_prompts/phase_1_foundation/plan.md
```

The plan is a working document — Step 2 (`02_review.md`) will critique it; Step 3 (`03_implement.md`) will execute it. Quality of this plan determines the quality of the build.

---

## What the plan must contain

Use this exact section structure. Use markdown headings. Be concrete and specific — vague plans get torn apart in review.

### 1. Build order & dependency graph

Enumerate every file in the spec's "Deliverables" tree. For each file, note:

- **Path** (exact, from spec)
- **Phase 1 needs:** what this file does in Phase 1 (a stub? full content? config?)
- **Depends on:** which other files must exist first, and why
- **Created in step:** which build step (a numbered step in section 2 below)

This is a table or list, not prose. Aim for ~30 entries.

### 2. Build steps (sequenced)

A numbered list of build steps, in execution order. Each step:

- **Goal** (one sentence)
- **Files touched** (list of paths)
- **Verification** (how you confirm this step worked before moving on — usually a shell command)
- **Rollback** (what to undo if this step fails)

Group related files into the same step where it makes sense (e.g. all `apps.py` stubs in one step). Aim for 12–20 steps.

The first few steps must be infra-readiness (uv project init, .python-version, .gitignore, .dockerignore) before anything Django-specific. The last steps are the full Compose smoke test against the 10 acceptance criteria.

### 3. Risk register

For each plausible failure mode, list:

- **Risk:** what could go wrong
- **Likelihood:** low / medium / high
- **Impact:** what breaks if it hits
- **Mitigation:** what you'll do proactively to prevent it
- **Detection:** how you'll notice if it happens

Cover at minimum:

- Torch sneaking into the image despite Phase 1 not declaring it
- `.env` accidentally committed
- Postgres healthcheck flapping during web boot
- Qdrant API key missing or empty causing 401s
- structlog config breaking Django/gunicorn log capture
- gRPC port confusion (6334 vs 50051)
- The `apps.<name>` vs `<name>` AppConfig.name foot-gun
- `uv sync` failing because `uv.lock` doesn't exist on first build
- Hot-reload not working in dev
- CI Postgres service container env mismatch

Plus any others you identify from the spec.

### 4. Verification checkpoints

Pause-and-verify points during the build. At each checkpoint, list the **exact commands** to run and the **expected outcome**. Examples:

- After uv project init: `uv sync` succeeds, `.venv/` created
- After Django config files: `uv run python -c "import django; from config import settings; print(settings.DEBUG)"` runs without errors
- After Compose + Dockerfile: `docker compose build` succeeds, image size under 800 MB
- After full stack up: all 10 acceptance criteria from `spec.md` pass

Aim for 6–10 checkpoints. Don't skip checkpoints to save time — they catch errors early.

### 5. Spec ambiguities & open questions

Read the spec critically. Wherever it underspecifies something, list it here with:

- **What's ambiguous**
- **Your proposed interpretation** (don't ask the user — make a call and justify it)
- **Reversibility:** if your call is wrong, how hard is it to fix later?

Examples of things to look for:
- Are there fields in `.env.example` that the spec lists but doesn't say where to read them in `settings.py`?
- Does the structlog config use `stdlib`-style or `processors`-style binding?
- Should the `web` service's startup command run `migrate` even though Phase 1 has no models?
- Does the test that accepts both 200 and 503 actually exercise the healthz code path under pytest, or is it a smoke test?

Aim for 5–10 entries. If you find none, you didn't read the spec carefully enough.

### 6. Files that are deliberately NOT created

The spec has an "Out of scope" section. Echo it back in your own words and add anything you noticed that could be tempting to write but shouldn't be (e.g. a `Tenant` model, an `IngestionPipeline` class, a chunker function). This list protects the build from scope creep.

### 7. Acceptance-criteria mapping

For each of the 10 acceptance criteria in `spec.md`, list:

- **Criterion** (one-line summary)
- **Which build step satisfies it**
- **Verification command**
- **Expected output**

Every criterion must map to at least one build step. If a criterion doesn't map cleanly, flag it as a spec gap in section 5.

### 8. Tooling commands cheat-sheet

The exact commands you'll use, with annotations. Examples:

```
uv init --python 3.13                  # bootstrap project (only first time)
uv add django>=5.2,<6.0 djangorestframework>=3.16 ...   # add prod deps
uv add --dev pytest pytest-django ruff mypy ...          # add dev deps
uv sync                                 # ensure venv matches lockfile
uv run python manage.py startproject config .            # bootstrap Django project
uv run ruff check .                     # lint
uv run pytest                           # run tests
docker compose build                    # build images
docker compose up -d                    # bring stack up
docker compose ps                       # see health
docker compose logs -f web              # tail web logs
docker compose down -v                  # full teardown including volumes
```

Note any non-obvious flag choices.

### 9. Estimated effort

A rough wall-clock estimate per build step. Use this only to surface places where a step is likely to take much longer than its neighbors (often a sign of hidden complexity).

### 10. Plan summary

A 3–5 sentence executive summary at the **top** of the document (above section 1, but write it last). Should answer:

- What's getting built?
- What's the riskiest part?
- How will the build verify itself?

---

## Output format

A single markdown file at `build_prompts/phase_1_foundation/plan.md`. Use clear headings (`##`, `###`), tables where they help, code blocks for commands. Aim for 400–800 lines. Longer than that means you're padding; shorter means you missed sections.

---

## What "done" looks like for this prompt

When you're finished, output to chat:

1. Confirmation that `plan.md` was created.
2. The total line count of the plan.
3. A 5-bullet summary of the plan's key sequencing decisions.
4. Any spec ambiguities flagged in section 5 (just the titles).

Then stop. Do **not** start implementing. Step 2 (`02_review.md`) reviews this plan before any code is written.
