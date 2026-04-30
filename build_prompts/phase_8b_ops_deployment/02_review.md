# Phase 8b — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **CRITIQUE the plan and revise it. No production code.**

---

## Required reading (in this order)

1. `build_prompts/phase_8b_ops_deployment/spec.md` — source of truth.
2. `build_prompts/phase_8b_ops_deployment/plan.md` — to critique.
3. `build_prompts/phase_8a_code_hardening/spec.md` — Phase 8a contract.
4. `build_prompts/phase_8a_code_hardening/implementation_report.md` — 8a outcomes.
5. `build_prompts/phase_7_search_grpc/spec.md` — gRPC contract for handler decorator.
6. `apps/core/middleware.py`, `apps/core/logging.py`, `apps/core/metrics.py`, `apps/grpc_service/handler.py`, `apps/ingestion/{embedder,pipeline}.py`, `apps/qdrant_core/search.py`, `Makefile`, `docker-compose.yml`, `.env.example` — current state.

If `plan.md` does not exist, abort.

---

## Your task

Adversarially review. Save:

- `build_prompts/phase_8b_ops_deployment/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_8b_ops_deployment/plan.md` — overwritten with revised plan

---

## Review lenses

For each: list findings (or `"no findings"`). Tag **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

- All 17 modified/new files addressed?
- All 27 hard constraints addressed (especially: Section 0 first, RequestID exclusion narrowed, ExtraAdder both chains, recorder helpers, gRPC decorator, embedder gauge, search gauge+histogram, bootstrap idempotency, snapshot/backup rotation, systemd unit optional, nginx grpc_pass, Compose stop_grace_period, CI no images, no new deps)?
- All 18 acceptance criteria mapped to steps?
- All 18 common pitfalls in risk register?
- Out-of-scope respected?

### Lens 2 — Edge cases the plan missed

- **Counter wiring in `AccessLogMiddleware` requires the response object.** Recorder must run AFTER `get_response(request)` returns but BEFORE the middleware exits. Plan should commit on placement (likely: in the `finally` block, after the access log emit).

- **`url_name` may be None for non-routed paths.** `request.resolver_match` is None for 404s. Plan should commit on the fallback (`endpoint = url_name or "unknown"`).

- **Pipeline phase recorder dispatch: per-phase or batched?** Plan needs to clarify: middleware iterates the `phases` dict and emits one `pipeline_phase_duration_seconds.observe(duration_seconds=value/1000.0).labels(phase=key)` PER ENTRY, OR batches all in one call. Per-entry is correct; batched is impossible for histogram observations. Plan should call this out.

- **Phase timer values are in MILLISECONDS in the access log but Prometheus histograms expect SECONDS.** Plan must document the conversion (`value_ms / 1000.0`) at the recorder call site.

- **gRPC decorator can't read `context.set_code(...)` retroactively.** The handler may set the code mid-method. Decorator needs to capture the code AT END (after the handler returns OR via exception type). Plan should commit on extraction strategy.

- **`embedder_loaded` gauge multi-worker visibility.** Each gunicorn worker has its own metrics view. Worker A loads BGE-M3 → its `/metrics` shows 1; Worker B hasn't loaded → its `/metrics` shows 0. Plan should document this (carry over from 8a) and confirm it's accepted.

- **`search_threshold_used` is a Gauge that gets overwritten on each search.** Multi-worker: each worker shows its OWN last-search threshold. Acceptable for v1 but document.

- **bootstrap.sh assumes the user can run docker after group add.** `sg docker -c ...` works for the immediate session; subsequent commands in the script also need `sg docker`. Plan should clarify: wrap the entire post-usermod commands in a single `sg docker -c "..."` block, or repeat per-command.

- **bootstrap.sh handles existing `.env` with stale value.** If `.env` exists but is missing required vars, `make health` will fail with cryptic error. Plan should commit on whether bootstrap validates `.env` keys against `.env.example`.

- **bootstrap.sh on a host where docker daemon isn't running.** Preflight check should `systemctl is-active docker` before proceeding. Plan should add.

- **snapshot_qdrant.sh credentials.** Qdrant API key is in `.env`. Script needs to source it. Plan should commit on `set -a; source .env; set +a` pattern.

- **backup_postgres.sh inside container vs host.** `pg_dump` available where? Plan should commit: invoke via `docker compose exec postgres pg_dump ...` or install postgresql-client on host? Recommend the former (no host deps).

- **Snapshot script can't snapshot a non-existing collection.** Returns 4xx from Qdrant API. Script should check existence first; or print a clear error.

- **CI workflow trigger on tags?** No tags planned for v1. Plan should commit: only PR + push to main.

- **CI ruff version pin.** Plan should commit: use the version from `pyproject.toml` dev deps (`ruff>=0.15.12`); use `uv run ruff` to invoke (matches local).

- **CI doesn't have docker access for compose.** Service containers replace it. Tests run directly via `pytest` in the runner, not in a container. Plan should commit on this.

- **CI Postgres connection string.** Tests use `tests.test_settings` (SQLite overlay) by default — no Postgres connection. Plan should commit: CI runs the SQLite-overlay test suite (matching `make test`), NOT against Postgres service container. Then we don't need the Postgres service container at all. **OR:** if we want CI to test against real Postgres, override `DJANGO_SETTINGS_MODULE` and add the service. Big decision — plan must commit.

- **Recommend: CI runs against SQLite overlay (matches local `make test` semantics).** Drops the Postgres + Qdrant service containers entirely. Faster CI, simpler config, matches existing test isolation.

- **systemd unit's `User=` directive.** Run as the deploy user, not root. Plan should commit on default value (env-templated).

- **systemd unit restart policy.** `Restart=on-failure` with `RestartSec=10s`. Plan should specify.

- **nginx config example file extension.** `.conf.example` so it doesn't auto-load if dropped in `/etc/nginx/sites-enabled/`. Plan should mention.

- **RUNBOOK section ordering.** Spec lists 10 sections but doesn't fix the order. Plan should commit. Recommend: deploy → upgrade → rollback → restart → logs → metrics → restore-postgres → restore-qdrant → rotate-secrets → failure-modes.

- **RUNBOOK lifecycle.** Document who maintains it (operators editing in production for env-specific notes) vs. who reviews it (devs on PRs).

- **Makefile `make load-test` precondition.** Stack must be running. Document "requires `make up` first" in the help string.

- **Counter values resetting on `make rebuild`.** Each rebuild re-creates the container; in-process counters reset. Document in RUNBOOK ("metrics reset on every restart"). Persistent counters require Prometheus's pull model — already what we're using.

### Lens 3 — Production-readiness gaps

- **bootstrap.sh leaves no audit log.** Recommend: tee output to `/var/log/qdrant_rag_bootstrap.log` for post-mortem.
- **No firewall guidance.** RUNBOOK should mention: ports 8080/50051 should NOT be public; nginx terminates on 443. Add a paragraph.
- **No log rotation for app logs.** Compose default is journald or default logging driver — plan should note.
- **Backups are not encrypted.** Local backups; if S3 hook used, document `aws s3 cp --sse` for encryption-at-rest.
- **No backup verification.** RUNBOOK should include a "test restore" recipe (restore to a temp DB, verify row counts).
- **CI doesn't run security scan.** Out of scope for v1; document Phase 9+ adds `pip-audit` or similar.

### Lens 4 — Pitfall coverage audit

For all 18 spec.md pitfalls.

### Lens 5 — Sequencing & dependency correctness

Critical sequence:
- Section 0 changes BEFORE rebuild #1.
- `metrics_recorders.py` BEFORE the imports from it.
- `ExtraAdder` BEFORE test updates.
- Stack rebuild AFTER Section 0.
- Verify metrics + access log AFTER rebuild.
- Section 1 scripts BEFORE Makefile + RUNBOOK that reference them.
- README last.

### Lens 6 — Verification command quality

Each verification step: strong / weak rationale.

### Lens 7 — Tooling correctness

- `make run python manage.py check` — uv-like wrapper.
- `make rebuild` — preserves volumes.
- `bash scripts/X.sh` — direct invocation; no make wrapper for the verification.
- `systemd-analyze verify` — confirms unit file syntax.
- `nginx -t` — confirms config syntax (may need to point at a substituted config in the dev env).
- `actionlint` — optional CI YAML linter; document if not available.

### Lens 8 — Risk register completeness

- Existing tests calling middleware/views — verify counter wiring doesn't break them.
- Existing tests asserting log line shape — verify the new fields appear without breaking old assertions.
- Phase 7.6 raw_payload tests — counter increments shouldn't affect them.
- Phase 7 backward-compat — gRPC handler decorator must preserve gRPC status codes correctly.

---

## Output structure

### File 1: `plan_review.md` (NEW)

Standard structure: sections per lens, summary, recommendation.

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
