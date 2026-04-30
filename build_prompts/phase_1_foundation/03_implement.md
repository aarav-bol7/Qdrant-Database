# Phase 1 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to BUILD what the revised plan describes, then VERIFY it against the spec, then REPORT honestly. Both halves matter — implementation without verification is half a job.**

---

## Required reading (in this order)

1. `build_prompts/phase_1_foundation/spec.md` — the source-of-truth specification. Re-read in full before you start.
2. `build_prompts/phase_1_foundation/plan.md` — the revised plan from Step 2. This is your roadmap.
3. `build_prompts/phase_1_foundation/plan_review.md` — the critique from Step 2. Read it so you understand *why* the plan is shaped the way it is. Don't re-litigate decisions already made.
4. `README.md` — for project context (skim).

If any of `spec.md`, `plan.md`, or `plan_review.md` is missing, abort with a clear error message — Steps 1 and 2 must run first.

---

## Hard rules during implementation

1. **Follow the revised plan.** It exists for a reason. Deviations are allowed only when the plan is provably wrong; document each deviation in the final report.
2. **Build in the order the plan specifies.** Don't reorder for speed — the plan's sequencing was reviewed for correctness.
3. **Run the plan's verification commands at every checkpoint.** If a checkpoint fails, stop and fix before proceeding. Don't accumulate broken state.
4. **Honor every "out of scope" item.** The spec is explicit. Do not write Tenant/Bot/Document models, embedders, chunkers, or `.proto` files. Phase 1 ends where the plan ends.
5. **No code comments unless the spec or plan justifies them.** Default is no comments.
6. **Never commit `.env`** — only `.env.example` goes in git.
7. **Never use `--no-verify`, `--force`, or destructive flags** without explicit reason in the plan.
8. **If `uv.lock` doesn't exist on first build, run `uv sync` (without `--frozen`) once to generate it, commit it, then use `uv sync --frozen` for repeatability.**
9. **If you discover a spec error mid-build,** stop, document it in the final report under "Spec defects discovered", and continue with your best interpretation. Do not silently improvise.
10. **No emoji in code or comments. No documentation files (`*.md`) beyond `implementation_report.md` unless the spec explicitly requires them.**

---

## Implementation phases

Execute the plan's build steps in order. The plan should already define these — this section just frames the rhythm.

### Phase A — Project bootstrap (steps that touch repo root)

`pyproject.toml`, `.python-version`, `.gitignore`, `.dockerignore`, `.env.example`, `manage.py`. After this phase: `uv sync` works.

**Verification:** `uv run python --version` reports 3.13.x; `.venv/` exists; no source code yet.

### Phase B — Django configuration (everything in `config/`)

`settings.py`, `urls.py`, `wsgi.py`, `asgi.py`, `celery.py`, `__init__.py`. After this phase: Django can boot in isolation against a stub database (with infra down, migration commands will fail, but `uv run python -c "import django; django.setup()"` should work after setting `DJANGO_SETTINGS_MODULE=config.settings`).

**Verification:** `uv run python -c "from config import celery_app; print(celery_app.main)"` prints `qdrant_rag`.

### Phase C — Apps skeleton (everything in `apps/`)

`apps/__init__.py` plus six app packages (`core`, `tenants`, `documents`, `ingestion`, `qdrant_core`, `grpc_service`), each with `apps.py` and (where the spec requires) `migrations/__init__.py`. The `core` app additionally gets `views.py`, `urls.py`, `logging.py`. After this phase: Django can resolve `INSTALLED_APPS` without errors.

**Verification:** `uv run python manage.py check` exits 0.

### Phase D — Healthz endpoint

Implement `apps/core/views.py:healthz`, the lazy QdrantClient singleton, and the structlog config in `apps/core/logging.py`. After this phase: `uv run python manage.py runserver` (with infra unreachable) returns a 503 from `/healthz` cleanly, with both subsystems reporting an error.

**Verification:** Manual curl against the running dev server. The error message must be a string, not a stack trace.

### Phase E — Container layer

`Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`. After this phase: `docker compose build` succeeds.

**Verification:** Built image size is sane (target < 800 MB; if it's larger, check whether torch slipped in via a transitive dep).

### Phase F — Stack-up & smoke

`docker compose up -d`. Wait for healthchecks. Run all 10 acceptance criteria from the spec.

**Verification:** Each acceptance criterion either passes or has a documented reason for failing (and that reason cannot be a pitfall).

### Phase G — Tests, scripts, CI

`tests/` directory with `test_healthz.py`, `tests/conftest.py`, `tests/__init__.py`. `scripts/verify_setup.py`, `scripts/compile_proto.sh`. `.github/workflows/ci.yml`. After this phase: `uv run pytest` passes; `uv run python scripts/verify_setup.py` exits 0 with the stack up.

**Verification:** Local pytest run is green. CI workflow file passes `actionlint` (or just look readable — CI must be defined but doesn't need to actually run on push for Phase 1 to be considered complete locally).

### Phase H — Final teardown & re-up

Confirm full repeatability: `docker compose down -v && docker compose up -d`. Re-run the 10 acceptance criteria. Everything must still pass on a clean rebuild.

---

## Self-review

After Phase H passes, **stop building**. Run this self-review against the spec — not the plan. The plan is a working document; the spec is the contract.

For each acceptance criterion in `spec.md` (all 10), record:

- **Pass / fail** (be honest — partial passes count as fails)
- **Verification command run** (paste it)
- **Output observed** (paste the relevant lines, redacting secrets)

For each pitfall in `spec.md` (all 10), record:

- **Avoided / hit / not-applicable**
- **How you confirmed** (the command or check that proves it)

For each "Out of scope" item in `spec.md`, record:

- **Confirmed not implemented**
- **Brief: where this would go in a future phase**

---

## Final report

Save to `build_prompts/phase_1_foundation/implementation_report.md`. Structure:

```markdown
# Phase 1 — Implementation Report

## Status
**OVERALL:** ✅ PASS / ❌ FAIL / ⚠️ PARTIAL

## Summary
- Files created: <N>
- Files modified: <N>
- Build duration (wall-clock): ~<minutes>
- Final image size: <MB>
- Tests passing: <N>/<N>
- Acceptance criteria passing: <N>/10

## Acceptance criteria (verbatim from spec.md)

### Criterion 1: <copy from spec>
- **Result:** ✅ PASS / ❌ FAIL
- **Command:** `<exact command>`
- **Output:**
  ```
  <relevant output, redacted>
  ```
- **Notes:** <any caveats>

[... repeat for all 10]

## Pitfall avoidance (verbatim from spec.md)

### Pitfall 1: <copy from spec>
- **Status:** Avoided / Hit / N/A
- **How confirmed:** <command or reasoning>

[... repeat for all 10]

## Out-of-scope confirmation

[... brief list of every out-of-scope item with "confirmed not implemented"]

## Deviations from plan

For each deviation:
- **What:** the change
- **Why:** plan was wrong / spec gap / runtime issue
- **Impact:** what's different from what the plan promised

## Spec defects discovered

Anything in `spec.md` that turned out to be incorrect, contradictory, or impossible. Do not silently work around — surface it.

## Outstanding issues

Anything that's not blocking Phase 1 acceptance but the user should know before Phase 2 starts. Examples:
- A docker compose warning that's harmless but noisy
- A version bump needed for compatibility with a transitive dep
- A test that's flaky and needs investigation

## Files created

A clean tree of every new file (paths only, no content). Use `find . -type f -not -path './.git/*' -not -path './.venv/*' -not -path './node_modules/*' | sort`.

## Commands to verify the build (one block, copy-pasteable)

```bash
docker compose down -v
docker compose up -d --build
sleep 60
docker compose ps
curl -fsS http://localhost:8000/healthz | python -m json.tool
uv run pytest -q
docker compose down -v
```

## Verdict

One paragraph: is Phase 1 ready to ship? What's the user's next step (proceed to Phase 2, fix outstanding issues, escalate spec defects)?
```

---

## What "done" looks like for this prompt

When finished, output to chat:

1. Path to `implementation_report.md`.
2. **Overall status: PASS / FAIL / PARTIAL.**
3. Acceptance criteria score (`X/10 passed`).
4. Any spec defects discovered.
5. Recommended next step for the user.

Then stop. The user reviews the report; if it's green, Phase 2's three prompts get generated.

---

## A note on honesty

If something is broken, say so. A failing build that's reported as failing is more useful than a passing build that's reported as passing but quietly skipped half the verification. The report is the contract — write it to be true, not flattering.
