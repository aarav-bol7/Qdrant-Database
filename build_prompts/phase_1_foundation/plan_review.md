# Phase 1 Plan Review

## Summary
- **Total findings:** 28
- **Severity breakdown:** 3 critical · 7 major · 18 minor
- **Plan accuracy:** ~92% spec compliance (gaps are sequencing + production-readiness, not omissions)
- **Recommendation:** **accept revised plan**. All critical/major findings have unambiguous fixes; no user escalation required.

The original plan covers every spec deliverable, hard constraint, stack version, acceptance criterion, and pitfall — files, structure, and risks are accurate. The defects are concentrated in **build-step sequencing** (settings.py / urls.py / `.env` ordering) and **production-readiness polish** (gunicorn log unification, thread-safe singleton, weaker verification commands). All resolvable inside the same plan structure.

---

## Findings by lens

### Lens 1 — Spec compliance

1. **[major] Test deviates from spec by dropping `@pytest.mark.django_db`.** Plan §6.3 (ambiguity 6.3) decides to drop the marker so the test runs without Postgres. But spec §"tests/test_healthz.py" gives the test verbatim **with** the marker. Spec is self-consistent: with the marker, pytest-django spins up a test DB, healthz's Postgres ping then succeeds; the 200/503 split is for Qdrant being up vs down. Where: `plan.md` §3 step 10 + §6.3. **How to fix:** keep the marker verbatim per spec; revise §6.3 to reflect that pytest *requires* Postgres (CI service container provides it; locally the dev runs `docker compose up -d postgres`). Drop the "drop the marker" interpretation.

2. **[minor] `verify_setup.py` exact stdout string not specified.** Spec mandates `[verify_setup] All checks passed.` on success. Plan §3 step 9 describes behavior but not the exact print string. Where: `plan.md` §3 step 9. **How to fix:** quote the exact print string in the step body.

3. **[minor] No "final report" step matching spec §"When you finish".** Spec asks for a report with file count + criteria results + deviations + ambiguities. Implementation prompt produces this; plan should reference it. Where: `plan.md` §3 (last steps). **How to fix:** add §3 step 21 "Phase-final report" pointing at `implementation_report.md`.

### Lens 2 — Missed edge cases

4. **[critical] Step 5 replaces `config/settings.py` with full content (INSTALLED_APPS pointing at `apps.core` etc.) before step 6 creates the apps.** If anyone runs `uv run python manage.py check` between steps 5 and 6, Django fails with `ModuleNotFoundError: No module named 'apps.core'`. Where: `plan.md` §3 step 5. **How to fix:** split step 5 — startproject only; defer the full settings.py replacement to step 7 (after apps + logging.py exist).

5. **[critical] Step 5 patches `config/urls.py` to `include("apps.core.urls")` before step 8 creates `apps/core/urls.py`.** Same class of error as #4 — `manage.py check` fails between steps 5 and 8. Where: `plan.md` §3 step 5. **How to fix:** defer the urls.py patch to step 8.

6. **[critical] `.env` not created until step 14, but checkpoint B (after step 7) calls `django.setup()` which evaluates `env("DJANGO_SECRET_KEY")` with no default — `ImproperlyConfigured` is raised.** Same blocker on step 8 (healthz view ping uses settings) and step 10 (pytest loads settings). Where: `plan.md` §3 step 14, checkpoint B/C in §5. **How to fix:** create local `.env` from `.env.example` immediately after step 3 (rename to step 3b or insert step 4b). Confirm `.env` is gitignored before any commit.

7. **[major] Step 1 verifies `docker compose version` but not that the Docker daemon is reachable.** `docker compose version` reads only the CLI binary; the daemon could be down. Where: `plan.md` §3 step 1. **How to fix:** add `docker info > /dev/null 2>&1 && echo daemon-ok` to step 1 verification.

8. **[major] Host port conflicts (5432, 6379, 6333, 6334, 8000, 50051) not preflight-checked.** First `docker compose up` will fail with `bind: address already in use` if anything else holds those ports. Where: `plan.md` §3 step 1 + step 17. **How to fix:** add `ss -ltn '( sport = :8000 or sport = :5432 or sport = :6333 or sport = :6334 or sport = :6379 or sport = :50051 )'` to step 1; abort if any are listening (or instruct user to free them).

9. **[minor] BuildKit assumption.** Dockerfile uses `--mount=type=cache,target=/root/.cache/uv` which requires BuildKit. Default-on in Docker Engine ≥23, but older installs may need `DOCKER_BUILDKIT=1`. Where: `plan.md` §3 step 11. **How to fix:** preflight `docker buildx version` in step 1; if absent, set `DOCKER_BUILDKIT=1` for build commands.

10. **[minor] Volume orphan accumulation across iteration.** During Phase 1 iteration the agent may run `down`/`up` repeatedly without `-v`. Volumes accumulate stale data (especially Postgres). Already mitigated in step 20 (`down -v`); restate as a guidance note. **How to fix:** add a tip in §9 cheat-sheet: between iterations of healthz wiring, prefer `down -v` to avoid stale Postgres state.

11. **[minor] CI vs local hostname divergence.** Already documented in plan §6.10 (CI uses `localhost`; local uses `postgres`). Restate the implication: CI's `.github/workflows/ci.yml` `env:` block must list every postgres + qdrant key, and they must point at `localhost` not the Compose name.

### Lens 3 — Production-readiness gaps

12. **[major] gunicorn access/error logs not unified with structlog.** Spec mandates "stdlib `logging` delegate to structlog so Django/DRF/gunicorn logs flow through the same pipeline." Default gunicorn writes its own format to stdout. Bridging requires either a custom `gunicorn.glogging.Logger` subclass OR a `dictConfig` that captures `gunicorn.access` and `gunicorn.error` loggers and routes them through `structlog.stdlib.ProcessorFormatter`. Where: `plan.md` §3 step 7. **How to fix:** add to apps/core/logging.py: a `LOGGING` dict (passed via `LOGGING_CONFIG = "logging.config.dictConfig"`) that lists `gunicorn.access`, `gunicorn.error`, `django`, `django.request`, `django.server` loggers — all delegating to a single `ProcessorFormatter` handler. Verify in step 18 that `docker compose logs web` shows access lines as JSON.

13. **[major] Module-level QdrantClient singleton lacks thread-safety.** Plan §3 step 8 says "lazy module-level singleton". Under gunicorn sync workers (single-threaded per process) it's fine; under any future threaded worker it races at first call. Where: `plan.md` §3 step 8. **How to fix:** wrap the lazy init in a `threading.Lock` (or `functools.lru_cache(maxsize=1)` which is internally locked). Cheap insurance.

14. **[minor] gunicorn `--graceful-timeout` vs Compose `stop_grace_period`.** Default gunicorn graceful is 30s; Compose default `stop_grace_period` is 10s — Compose SIGKILLs before gunicorn drains. In-flight requests drop on `compose down`. Phase 1 has no in-flight requests of consequence (only healthz). Defer to Phase 8 hardening with a risk register entry.

15. **[minor] `/readyz` (readiness) separated from `/healthz` (liveness).** K8s convention. Spec only requires `/healthz`. Phase 1 conflates them. Acceptable for v1. No fix; note as deferred.

16. **[minor] No CPU/memory limits on containers.** Phase 4 will need them (BGE-M3 ~1.8 GB RAM). Spec doesn't mandate for Phase 1. Defer.

17. **[minor] Django 500 stack-trace exposure under DEBUG=False.** Django default behavior is the generic 500 page; stack traces logged but not shown to clients. Already correct out of the box. No action needed.

18. **[minor] Connection-string secret leak via stray repr.** No code path in Phase 1 emits `repr(settings.DATABASES)` or similar. Vacuously safe.

### Lens 4 — Pitfall coverage audit

| Pitfall | Plan covers? | Verification catches? |
|---|---|---|
| 1 — Torch sneaking in | ✓ risk #1 | ✓ multiple `uv pip list \| grep -i torch` checks |
| 2 — `.env` committed | ✓ risk #2 | ✓ `git ls-files .env` |
| 3 — Postgres healthcheck flapping | ✓ risk #3 | △ indirect (logs grep) — acceptable |
| 4 — Qdrant API key empty | ✓ risk #4 | ✓ healthz error string |
| 5 — gRPC port 6334 vs 50051 | ✓ risk #6 | △ requires reading rendered YAML manually |
| 6 — Hot reload broken | ✓ risk #9 | ✗ verification only checks venv intact |
| 7 — structlog output not JSON | △ partial via risk #5 | ✗ no explicit prod-mode test |
| 8 — `apps.<name>` foot-gun | ✓ risk #7 | ✓ `grep -h 'name = ' apps/*/apps.py` |
| 9 — Missing apps/__init__.py | ✓ risk #15 | ✓ find count |
| 10 — CI POSTGRES env mismatch | ✓ risk #10 | △ first-push validation only |

19. **[minor] Pitfall 6 verification is weak.** Plan's risk #9 detection only confirms the venv is importable inside the container; doesn't actually test reload-on-edit. **How to fix:** add an explicit recipe: `echo "# noop" >> apps/core/views.py && docker compose logs --tail 5 web` should show runserver detecting the change. Optional polish.

20. **[major] Pitfall 7 verification missing.** The override file forces `DJANGO_DEBUG=True` so dev logs are kv. To prove JSON in prod-mode, the verification must run `docker compose -f docker-compose.yml up -d` (no override) and inspect logs. Where: `plan.md` §3 step 18 criterion 8. **How to fix:** add a prod-mode log inspection sub-step.

21. **[minor] Pitfall 5 verification.** Inspecting `docker compose config` shows the YAML; agent might read it sloppily. **How to fix:** add `docker compose port qdrant 6334` and `docker compose port grpc 50051` calls — Compose will print the host:port mapping unambiguously.

### Lens 5 — Sequencing & dependency correctness

22. **[critical] Settings.py / urls.py / .env ordering.** Already enumerated as findings #4, #5, #6. Re-stated here so the sequencing audit is honest. The build steps must be re-sequenced so:
    - Step 5 = startproject + minimal patches (config/__init__.py celery import, config/celery.py creation). settings.py keeps default INSTALLED_APPS. urls.py keeps default.
    - Step 6 = apps/ + AppConfig stubs (independent of settings.py).
    - Step 6.5 = create local `.env` from `.env.example`.
    - Step 7 = create apps/core/logging.py FIRST, then patch settings.py (env-driven config + INSTALLED_APPS for all apps + DRF + structlog wiring at bottom).
    - Step 8 = apps/core/views.py + apps/core/urls.py, then patch config/urls.py to include them.

23. **[major] Step 7 internal ordering.** logging.py and settings.py are touched in the same step but settings.py imports `apps.core.logging.configure_logging`. **How to fix:** explicit sub-order — write `apps/core/logging.py` first, then patch `config/settings.py`.

### Lens 6 — Verification command quality

24. **[minor] Step 1 verification doesn't probe daemon.** Already covered in finding #7.

25. **[minor] Manual `runserver + curl + kill` in step 8.** Brittle background-process management. **How to fix:** replace with the Django test client pattern: `uv run python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup(); from django.test import Client; r = Client().get('/healthz'); print(r.status_code, r.content.decode())"`. No background process needed.

26. **[minor] `manage.py check` is the strongest single command for Django wiring health.** Plan uses ad-hoc `python -c "import django; django.setup()"`. **How to fix:** prefer `uv run python manage.py check` at the end of steps 6, 7, 8.

27. **[minor] Criterion 8 JSON validation should use `jq`.** Plan currently says "each line is valid JSON or kv-formatted with `service=qdrant_rag`" — not actually verified. **How to fix:** `docker compose -f docker-compose.yml logs web --tail 20 | grep -E '^{' | head -1 | jq -e .` (exit 0 means valid JSON line found).

### Lens 7 — Tooling correctness

28. **[minor] CI uses `astral-sh/setup-uv@v5` AND `actions/setup-python@v5`.** `setup-uv` itself accepts a `python-version` input and installs Python. Two actions is redundant. **How to fix:** use only `astral-sh/setup-uv@v5` with `python-version: 3.13`.

(no other findings — `uv add`, `uv sync` flag use, `docker compose` v2 invocation are all correct.)

### Lens 8 — Risk register completeness

Add four risks to §4. All [minor].

- **gunicorn access logs not JSON.** Likelihood medium, impact medium (criterion 8 silently passes if you only check Django logs). Mitigation: bridge via dictConfig (finding #12). Detection: `jq -e .` on `docker compose -f docker-compose.yml logs web | head`.
- **Host port conflicts.** Likelihood medium, impact high (whole stack fails to start). Mitigation: preflight `ss -ltn`. Detection: Compose `up` exit code + error message.
- **Postgres test DB CREATEDB role.** Likelihood low, impact medium. By default `pytest-django` calls `CREATE DATABASE`; service-container Postgres user is superuser so this works in CI. Locally, the user's compose Postgres user is also superuser via the env vars. Mitigation: ensure CI + local Postgres env grants. Detection: pytest logs.
- **Dockerfile uv image `:latest` non-determinism.** Likelihood low, impact low (build reproducibility on a different day). Mitigation: pin `ghcr.io/astral-sh/uv:0.9` (or whatever current is). Spec uses `:latest`; defer to optional polish.

---

## Findings escalated to user

**None.** Every finding has a clear mechanical fix that doesn't require taste judgements or external information. The revised plan addresses all 3 critical and all 7 major findings without leaving any `⚠️ User decision needed` callout.

Recommendation: proceed to Prompt 3 (implementation) on the revised plan immediately.
