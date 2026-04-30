# Phase 1 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to CRITIQUE the plan from Step 1 and then revise it. Do not write production code. Do not start docker. The only file you create is the revised plan.**

---

## Required reading (in this order)

1. `build_prompts/phase_1_foundation/spec.md` — the source-of-truth specification. Read first.
2. `build_prompts/phase_1_foundation/plan.md` — the plan produced by Step 1. Read after the spec, with the spec fresh in your head, so you can compare.
3. `README.md` — for project context (skim).

If `plan.md` does not exist, abort with a clear error message — Step 1 must run first.

---

## Your task

Adversarially review `plan.md`. Find every gap, wrong assumption, missed edge case, and production-readiness flaw. Then produce a **revised plan** that addresses every finding.

Save outputs to:

- `build_prompts/phase_1_foundation/plan_review.md` — the critique findings (this is a new file)
- `build_prompts/phase_1_foundation/plan.md` — **overwritten** with the revised plan

Keep the original plan's structure (sections 1–10) — your revision adds, corrects, or extends. Don't restructure for the sake of it.

---

## Review lenses

Apply each lens systematically. For each lens, list findings even if you find none (write "no findings" so the user can trust you actually checked). Be skeptical — if a finding is debatable, surface it anyway.

### Lens 1 — Spec compliance

For each requirement in `spec.md`, verify the plan addresses it. Walk through:

- Every file in the deliverables tree
- Every hard constraint (the 9 in spec.md's "Hard constraints" section)
- Every locked stack version
- Every acceptance criterion (all 10)
- Every pitfall (all 10)
- The "Out of scope" list — does the plan respect it?

Flag any requirement that the plan doesn't mention or handles incorrectly.

### Lens 2 — Edge cases the plan missed

Go through each step in the plan and ask: "what could happen here that the plan didn't anticipate?" At minimum, check for:

- **First-run vs subsequent-run divergence:** does the plan account for `uv.lock` not existing before first `uv sync`? For Postgres volume not existing before first `up`?
- **Partial failure:** what happens if `docker compose up` succeeds for 5 of 6 services and one stays unhealthy? Does the plan have a recovery path?
- **Permissions:** does any volume mount need specific UID/GID? Does the bge_cache volume need pre-creation?
- **Network:** what if the Docker daemon's default bridge conflicts with `qdrant_rag_net`? What if port 8000 is already taken on the host?
- **Versioning drift:** if a Phase 4 dep update bumps Django minor, does the plan still apply?
- **Concurrency:** does `docker compose up` race-condition the migrate command in `web` before Postgres is fully ready, despite `service_healthy`?
- **Data:** does the plan accidentally bake any data into images that should be at runtime only?
- **Time:** is the `web` healthcheck `start_period: 30s` enough for `uv sync` + `migrate` + first gunicorn boot on a slow CPU?
- **Repeatability:** can the build be re-run from scratch (`docker compose down -v && docker compose up`) and still pass all 10 acceptance criteria?
- **CI vs local:** does the CI workflow actually exercise what the local dev flow exercises? If not, what's the divergence?

Add any other edge cases you think of.

### Lens 3 — Production-readiness gaps

Phase 1 must ship a foundation that's production-grade, not a prototype. Check:

- **Logging:** are stdout logs structured (JSON) in prod mode? Will gunicorn's access log appear alongside Django logs in the same stream?
- **Healthcheck robustness:** does the `/healthz` endpoint timeout properly? What's the response if Qdrant is reachable but returns a 401 (wrong API key)? Does the endpoint differentiate "Postgres unreachable" from "Postgres slow"?
- **Graceful shutdown:** does the plan address gunicorn handling SIGTERM with `--graceful-timeout`? Does `docker compose down` with the default 10-second grace work for the `web` container, or do in-flight requests drop?
- **Secrets:** are there any secrets that leak into logs? (e.g. structlog accidentally logging the Postgres connection string with password.)
- **Resource limits:** does the plan set any container CPU/memory limits? If not, is that a deliberate Phase 1 deferral or an oversight?
- **Backup hygiene:** Phase 1 doesn't ship backups, but does it create the volume topology that allows backups in Phase 8?
- **Observability stub:** is `/healthz` enough, or should there also be a `/readyz` (readiness vs liveness distinction)?
- **Error pages:** does Django's default 500 page leak stack traces if `DEBUG=False`? Is there a placeholder 500 handler?

### Lens 4 — Pitfall coverage audit

The spec lists 10 pitfalls. For each, verify:

1. The plan explicitly addresses it in a build step or risk register entry.
2. The verification commands would actually catch the pitfall if it occurred.

If pitfall #1 (torch sneaking in) is in the risk register but the plan's verification commands wouldn't catch it, that's a finding.

### Lens 5 — Sequencing & dependency correctness

Walk the plan's build steps in order. For each step, ask:

- **Does this step need anything from a later step?** (a circular dependency means the plan is wrong)
- **Could this step be done earlier?** (a delayed step means the plan over-sequences and slows the build)
- **What if I stop after this step?** (does the project leave the working dir in a coherent state if interrupted?)

A correct plan has a strict topological order with no circularities and no premature steps.

### Lens 6 — Verification command quality

For each verification command in the plan:

- Does it actually verify the goal of the step, or just confirm the command exited 0?
- Is it the cheapest reliable check, or is it overkill?
- Does it produce output that's actually useful when it fails?

Replace weak verifications with stronger ones.

### Lens 7 — Tooling correctness

Spot-check the cheat-sheet commands:

- Are flags up-to-date with current `uv` (the project specifies `uv` latest as of 2026)?
- Is `uv add` the right command, or should it be `uv pip install` for compatibility with `pyproject.toml`?
- Does `uv sync --frozen` work when `uv.lock` doesn't exist? (It doesn't — handle the first-time case.)
- Are docker compose commands using `docker compose` (v2) or the deprecated `docker-compose` (v1)?

### Lens 8 — Risk register completeness

Are there risks the plan missed? Examples worth checking:

- Apple Silicon vs x86_64 wheel availability (only matters if anyone builds locally on macOS — likely not the user's box, but CI might be x86_64 GitHub runners).
- Disk-space exhaustion during Docker image builds (multi-stage builds can leave intermediate layers).
- DNS resolution inside the Compose network (sometimes `postgres` doesn't resolve until the network is fully up).
- structlog `make_filtering_bound_logger` requiring the logging level to be set early enough.
- `psycopg[binary]` vs `psycopg[c]` — the binary wheel includes its own libpq; do we still need `libpq5` in the runtime image? (Yes, but verify.)
- Django's `STATIC_ROOT` write permissions — does the runtime user have write access?

---

## Output structure

### File 1: `plan_review.md` (NEW)

A critique document. Structure:

```markdown
# Phase 1 Plan Review

## Summary
- Total findings: <N>
- Severity breakdown: <X critical, Y major, Z minor>
- Plan accuracy: <%> spec compliance
- Recommendation: <accept revised plan / re-plan from scratch / escalate to user>

## Findings by lens

### Lens 1 — Spec compliance
1. **[severity] Title.** Description. Where in plan: <section/line>. How to fix: <action>.
2. ...

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

Anything that requires a user decision before Step 3 can run. Each entry: what, why it can't be auto-resolved, options.
```

Use severity tags: **[critical]** = blocks the build, **[major]** = will cause a re-do, **[minor]** = polish.

### File 2: `plan.md` (OVERWRITE)

The revised plan. Same structure as the original (sections 1–10), but:

- Every critical and major finding from `plan_review.md` is addressed in the relevant section.
- Add a section 0 at the top: **"Revision notes"** — list what changed vs the previous version, with cross-references to `plan_review.md` finding numbers.
- Minor findings can be batched into a single risk-register entry or accepted as-is — note your call.
- If a finding can't be resolved without user input, leave the original text in place and add a `> ⚠️ User decision needed: ...` callout pointing to `plan_review.md`.

---

## What "done" looks like for this prompt

When finished, output to chat:

1. Confirmation that both files were saved (`plan_review.md` created, `plan.md` overwritten).
2. The finding count broken down by severity (critical / major / minor).
3. Any findings escalated to the user (titles only).
4. Your recommendation: is the revised plan ready for Step 3, or does the user need to weigh in first?

Then stop. Step 3 (`03_implement.md`) executes the revised plan only after the user confirms (or accepts the auto-resolution of all findings).
