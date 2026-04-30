# Phase 1 Fix — Implementation Report

## Status
**OVERALL: PARTIAL**

The fix per `fix_plan.md` was applied verbatim to `docker-compose.override.yml` and `build_prompts/phase_1_foundation/spec.md`. Host-side regression checks (ruff, pytest, verify_setup, merged Compose YAML render) all PASS. **Docker-level verification is blocked by the same daemon-socket permission issue carried over from the previous Phase 1 implementation session — the user `bol7` still is not in the `docker` group, so `docker compose down -v && docker compose up -d --build` cannot run from this agent's shell.** Whether the fix actually clears the original `Downloading <pkg>` symptom inside the `web` container can therefore only be confirmed by the user running the verification commands listed below after granting docker access.

## Summary
- **Files modified:** 3 (expected: 2 — see "Out-of-scope confirmation" for the third file's justification).
- **Original error:** "web container `(unhealthy)` because `docker-compose.override.yml`'s `- .:/app` bind shadows the image's `/app/.venv`, causing `uv run` to re-sync at every container start; logs show only `Downloading <pkg>` lines and never reach gunicorn before the healthcheck times out."
- **Original error reproduces?** **UNVERIFIED — cannot run docker.** The empirical preconditions for the diagnosis are confirmed (root-owned residual venv files left by the prior broken Compose run; recovered via `mv` since the project root is bol7-owned). The applied fix is mechanically correct vs `fix_plan.md`. Verification awaits docker access.
- **Phase 1 acceptance criteria 5, 6, 7, 8, 10 status:** PENDING — same docker-access blocker.

## Diffs applied

### File 1: `docker-compose.override.yml`

```diff
@@ services.web.volumes
     volumes:
       - .:/app
+      - /app/.venv
       - bge_cache:/app/.cache/bge

@@ services.worker.volumes
   worker:
     volumes:
       - .:/app
+      - /app/.venv
     restart: "no"
```

**Reason:** anonymous volume `- /app/.venv` overlays the bind mount on both `web` and `worker`, exposing the image's prebuilt venv instead of the host's foreign-Python-path venv. Per `fix_plan.md` §3 file 1 verbatim.

### File 2: `docker-compose.yml`

**No change** (anonymous volume needs no top-level declaration). Per `fix_plan.md` §3 file 2.

### File 3: `build_prompts/phase_1_foundation/spec.md`

Two edits in this file. Pitfall #6 rewritten in place (numbering of #7–#13 preserved). Override example updated to match the actual code edit byte-for-byte.

```diff
@@ docker-compose.override.yml example block (web volumes)
     volumes:
       - .:/app
+      - /app/.venv
       - bge_cache:/app/.cache/bge

@@ docker-compose.override.yml example block (worker volumes)
   worker:
     volumes:
       - .:/app
+      - /app/.venv
     restart: "no"

@@ Common pitfalls #6 (line 810)
-6. **Hot reload doesn't work in dev.** The override file mounts the project
-   root at `/app`, which shadows the built venv. uv's venv lives at
-   `/app/.venv` which is itself shadowed. Solution: use a named volume for
-   `.venv` (already pattern in similar setups) or accept that you need to
-   rebuild after `pyproject.toml` changes. For Phase 1, accept the rebuild
-   — premature optimization.
+6. **Bind-mounted source shadows the image's venv.** The dev override mounts
+   the host project root at `/app` for hot reload. Without an additional
+   escape, this hides the image's `/app/.venv` and the container falls back
+   to the host venv (which has foreign Python interpreter paths) — `uv run`
+   then re-runs `uv sync` at every container start, downloads every wheel
+   from scratch, and the web healthcheck times out before gunicorn ever
+   starts. **Required fix:** add an anonymous volume at `/app/.venv` for
+   both the `web` and `worker` services in the override (`- /app/.venv` on
+   its own line, immediately after `- .:/app`). The anonymous volume
+   initialises from the image's venv on first container start and stays
+   out of the host's way. **Trade-off:** after editing `pyproject.toml` or
+   `uv.lock`, run `docker compose down -v && docker compose up -d --build`
+   to drop the stale venv volume and recreate it from the rebuilt image.
+   Plain `docker compose restart` (and `stop`/`start`) keeps the existing
+   container and reuses the old anonymous volume — only container
+   recreation (via `up --build` or `down`/`up`) replaces it. Prod-style
+   runs (`docker compose -f docker-compose.yml up`) are unaffected because
+   there is no bind mount to shadow the image's venv.
```

A short paragraph was also added immediately after the override example block, summarising why the `- /app/.venv` lines are present (one sentence pointing forward to pitfall #6).

## Verification checklist results

```
[ ] Bring stack down with volumes wiped:
    docker compose down -v
    → BLOCKED: "permission denied while trying to connect to the docker API
      at unix:///var/run/docker.sock"
      Cause: user `bol7` not in `docker` group (carried over from prior
      Phase 1 session). Fix: `sudo usermod -aG docker bol7 && newgrp docker`.

[ ] Rebuild and start:
    docker compose up -d --build
    → BLOCKED: same permission error.

[ ] Wait for healthchecks:
    sleep 60
    → N/A (no stack to wait on)

[ ] Inspect web logs — the smoking gun:
    docker compose logs --tail 100 web
    → BLOCKED: same permission error.
    PROXY EVIDENCE that the diagnosis is correct: the host's `.venv` was
    found root-owned (created by the prior broken Compose run's runtime
    `uv sync`, which ran as root inside the container and wrote into the
    bind-mounted host directory). `head -3 .venv/pyvenv.cfg` showed
    `home = /usr/local/bin` — a CONTAINER Python path written into the
    HOST venv, the exact corruption the bind-shadowing diagnosis predicts.
    After `mv .venv .venv.broken-by-compose && uv sync --frozen`, the new
    host `.venv/pyvenv.cfg` correctly shows
    `home = /home/bol7/.pyenv/versions/3.13.0/bin`.

[ ] All containers healthy or running:
    docker compose ps
    → BLOCKED.

[ ] Healthz returns 200 with both subsystems OK:
    curl -fsS http://localhost:8000/healthz | python -m json.tool
    → BLOCKED.

[ ] Healthz failure mode (acceptance criterion 7):
    docker compose stop qdrant + curl + start
    → BLOCKED.

[ ] Logs are JSON in prod mode (acceptance criterion 8):
    docker compose -f docker-compose.yml up -d ... | grep -E '^\{.*"event":.*\}$'
    → BLOCKED. Host-side proxy: structlog JSON format already verified
    end-to-end in implementation_report.md (`logging.getLogger('boot').info(...)`
    produces a valid JSON record with `service: qdrant_rag, version: 0.1.0-dev`).

[ ] Repeatability — clean rebuild still passes (acceptance criterion 10):
    docker compose down -v && up -d --build && curl /healthz
    → BLOCKED.

[X] Existing tests still green (regression check):
    uv run pytest -q
    → 1 passed in 2.58s. (warnings about Qdrant client closing channels are
      cosmetic — same as before the fix.)

[X] Linter still clean:
    uv run ruff check .
    → All checks passed!  (0 violations)

[X] Format check still clean:
    uv run ruff format --check .
    → 30 files already formatted. (`activate_this.py` inside the orphaned
      `.venv.broken-by-compose/` was the only "would reformat" — added that
      directory to .gitignore so future passes ignore it. See "Out-of-scope".)

[X] verify_setup.py still works:
    uv run python scripts/verify_setup.py
    → exit=1; "[verify_setup] FAIL postgres: ... failed to resolve host
      'postgres': Temporary failure in name resolution"
    Expected fail-path output (no Compose stack up). Pass-path will be
    verified once docker access is restored.

[ ] Out-of-scope guard — confirm by listing:
    git status --short
    → N/A: project is NOT a git repository (no .git dir; `git status`
      reports "not a git repository"). Modified-files audit done by
      direct file inspection instead — see "Out-of-scope confirmation".

[X] Spec ↔ code alignment:
    Confirmed via `diff` of the rendered Compose merged config and the
    spec.md example block. Both contain the new `- /app/.venv` line for
    web AND worker. The merged Compose YAML (`docker compose -f
    docker-compose.yml -f docker-compose.override.yml config`) shows the
    anonymous volume entry for both services with `target: /app/.venv`
    and no source (anonymous).

[X] Multi-tenant guard / Identifier guard / Payload completeness:
    Vacuously true. The fix touches only YAML (Compose override, spec doc).
    No Python code, no business logic, no view, no model, no helper
    function added or removed. There is no new code path that could
    accept tenant_id from a request body or construct a collection name.
```

## Confirmation: original error no longer reproduces

**STATUS: UNVERIFIED — docker access blocked.**

`docker compose logs --tail 100 web` cannot run because `docker compose` itself returns `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`. The fix is mechanically correct vs `fix_plan.md` §3 (which the PROMPT 2 review accepted with 0 critical / 0 major findings). Empirical verification — and specifically the **smoking-gun assertion that `docker compose logs web | grep -c '^Downloading'` returns `0`** — must be performed by the user after granting docker access.

Two pieces of indirect evidence support the diagnosis being correct AND the applied fix being the right shape:
1. The host's `.venv` directory was found root-owned (from the prior broken Compose run that wrote into the bind-mounted host venv). After recovery, the host venv has the correct Python interpreter path. The applied anonymous volume prevents this corruption from recurring.
2. `docker compose -f docker-compose.yml -f docker-compose.override.yml config` (the merged YAML render, which DOES NOT need daemon access) successfully shows both `web` and `worker` with the new anonymous volume entry — the YAML is syntactically and semantically correct.

## Out-of-scope confirmation

Files modified for this fix:

| Path | Edit | In-scope per fix_plan? |
|---|---|---|
| `docker-compose.override.yml` | added `- /app/.venv` to web volumes (line 8) and worker volumes (line 27) | YES — fix_plan.md §3 file 1 |
| `build_prompts/phase_1_foundation/spec.md` | rewrote pitfall #6 (line 810); updated docker-compose.override.yml example block to add `- /app/.venv` lines | YES — fix_plan.md §3 file 3 |
| `.gitignore` | appended `.venv.broken-by-compose/` (line 36) | **NO — forced workaround for original bug's residual** (see below) |

`docker-compose.yml` was NOT modified, per the anonymous-volume choice (no top-level `volumes:` declaration needed).

### The 4th-file deviation: `.gitignore`

**Why:** while attempting to run host-side verifications, `uv` refused to use the existing host `.venv` (it had a broken Python interpreter symlink — a symptom that `pyvenv.cfg` showed `home = /usr/local/bin`, the *container* Python path, written by the prior broken Compose run that ran as root inside the container and wrote into the bind-mounted host venv). The recovery: `mv .venv .venv.broken-by-compose && uv sync --frozen`. The mv'd directory contains root-owned files that this agent cannot delete without sudo. To keep ruff/pytest from scanning the orphaned tree (and to keep a future `git init && git status` from showing it as untracked), `.venv.broken-by-compose/` was added to `.gitignore`.

**Why this is OK:** the .gitignore line is a residual cleanup of the **original** bug, not new behavior. Once the user runs `sudo rm -rf .venv.broken-by-compose`, the .gitignore entry can be reverted (it's harmless either way).

**Justification per HARD RULES #2:** "Touch at most three files... Any other file edit is a deviation that must be flagged and justified in the report." This deviation is flagged and justified here.

## Already-working invariants verified intact

- **All Python source unchanged.** No `apps/`, `config/`, `tests/`, or `scripts/` files modified. (Verified by direct file inspection — git is not initialised here, but the file mtimes and contents are untouched.)
- **`Dockerfile` unchanged.** Same multi-stage build, `PATH="/app/.venv/bin:${PATH}"`.
- **Healthchecks unchanged.** `postgres`, `redis`, `qdrant`, `web` healthcheck definitions in `docker-compose.yml` are byte-identical.
- **structlog config unchanged.** `apps/core/logging.py` is the same file that emits the JSON record verified in implementation_report.md.
- **`pytest` still green.** Output: `1 passed`. Cosmetic warnings about Qdrant client closing channels are unchanged from before the fix.
- **`ruff check` still clean.** Output: `All checks passed!`
- **`ruff format` still clean for project code.** The only "would reformat" is `activate_this.py` inside `.venv.broken-by-compose/`, which is excluded via the .gitignore addition above.
- **`verify_setup.py` exit codes correct.** `exit=1` with the expected `[verify_setup] FAIL postgres: ...` message when no infra is up.
- **`docker compose -f docker-compose.yml -f docker-compose.override.yml config -q`** exits 0 — the merged YAML is syntactically valid.
- **Spec pitfall numbering preserved.** `#1`–`#5` and `#7`–`#13` untouched; only `#6` rewritten in place. spec.md actually has 13 pitfalls now (the user added `#11`–`#13` after Phase 1 implementation, presumably incorporating the deviations from `implementation_report.md`).

## Outstanding issues

These are the same blockers that prevented full Phase 1 verification originally. None are caused by this fix.

1. **Docker socket permission denied for user `bol7`.**
   - Symptom: every `docker compose ...` returns `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`.
   - Cause: `/var/run/docker.sock` is `srw-rw---- root docker`; `bol7` is not in `docker` group.
   - Fix:
     ```bash
     sudo usermod -aG docker bol7
     newgrp docker        # or log out and back in
     ```

2. **Host port conflicts on 5432, 6379, 8000.**
   - Symptom: `docker compose up -d` will fail to bind these ports.
   - Cause: host PostgreSQL, host Redis, and the user's other Django runserver (PID 1465244 last session — may be different now) are listening.
   - Fix:
     ```bash
     sudo systemctl stop postgresql redis-server
     pkill -f 'DynamicADK.*manage.py runserver'   # or kill the current PID
     ```

3. **Orphaned `.venv.broken-by-compose/` directory (root-owned).**
   - Symptom: takes ~150 MB; ruff scans into it unless gitignored.
   - Cause: the original Compose bug's runtime `uv sync` ran as root inside the container and corrupted the bind-mounted host `.venv`. Recovery required `mv` (not `rm`) because of root ownership.
   - Fix:
     ```bash
     sudo rm -rf .venv.broken-by-compose
     ```
     Optional: revert the `.gitignore` line (the `.venv.broken-by-compose/` entry on line 36).

## Verdict

The fix per `fix_plan.md` is **mechanically applied and correct**. The two YAML edits (`docker-compose.override.yml`'s web + worker volumes lists) introduce the anonymous-volume escape; the spec.md edits update pitfall #6 and the override example to match. Host-side invariants (ruff, pytest, format, verify_setup, merged Compose YAML render) all PASS. The empirical proof that the original `(unhealthy)` web symptom is gone — specifically the assertion that `docker compose logs --tail 100 web | grep -c '^Downloading'` returns `0` — **cannot be produced from this agent's shell** because the docker daemon socket is not accessible to user `bol7`. **User's next step:** run the three sudo commands from "Outstanding issues" (group fix, host service stops, orphan cleanup), then run the post-fix verification block from `fix_plan.md` §4. If all those pass, Phase 1 acceptance criteria 5, 6, 7, 8, 10 should flip from PENDING to PASS, and Phase 2 (Domain Models) is unblocked.
