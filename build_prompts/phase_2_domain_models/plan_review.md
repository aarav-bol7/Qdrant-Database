# Phase 2 Plan Review

> Adversarial critique of `build_prompts/phase_2_domain_models/plan.md` (revision 1, 470 lines). Reviewer: a separate pass over the same plan, with the spec re-read fresh.

---

## Summary

- **Total findings:** 21
- **Severity breakdown:** **0 critical**, **2 major**, **19 minor**
- **Plan accuracy vs spec:** ~95% spec coverage. Every deliverable, hard constraint, acceptance criterion, and out-of-scope item is addressed. Minor gaps in pitfall surfacing (#5, #7, #8, #9) and a few weak verification commands.
- **Recommendation:** **Accept revised plan and proceed to Prompt 3.** The one HIGH-impact ambiguity (Document `bot` FK vs `bot_id` CharField column collision) was already escalated correctly in the original plan as §6 ambiguity #1. No new critical findings emerged from this review. The two major findings (L6.1 verification strength, L7.1 HTTP_PORT) are mechanical corrections.

---

## Findings by lens

### Lens 1 — Spec compliance

**L1.1 — [minor] Deliverables count is 10, not 9.**
Spec.md §"Deliverables" closes with "That's 9 new / replaced files." The actual tree contains 10 entries (validators.py, tenants/models.py, tenants/admin.py, tenants/migrations/0001_initial.py, documents/models.py, documents/admin.py, documents/migrations/0001_initial.py, naming.py, test_models.py, test_naming.py). The plan's §2 inherits the off-by-one without flagging.
- Where in plan: §2, table contains all 10 entries — correct contents but the surrounding prose echoes "9 entries" indirectly.
- How to fix: §2 already lists 10 rows. Add a note in §0 (revision notes) that the spec text says "9" but the tree has 10. No build impact.

**L1.2 — [minor] Hard constraint #2 (no new dependencies) not surfaced as an explicit guardrail.**
Phase 2 must not run `uv add`. The plan doesn't mention this in §3 or §4. Easy to overlook in heat of build.
- How to fix: Add a one-line risk register entry: "If `uv sync` is needed during Phase 2 build, you're solving the wrong problem." Verification: `git diff pyproject.toml uv.lock` shows zero changes.

**L1.3 — [minor] Hard constraint #11 (no comments unless justified) not echoed.**
- How to fix: One-line note in §3 preamble: "Code follows spec body verbatim. Spec body is comment-free; do not add commentary."

**L1.4 — [minor] Spec pitfall #5 (collection_name manually set) absent from risk register.**
- How to fix: Risk #18: "Caller manually sets `Bot.collection_name` in shell or admin. Mitigation: `Bot.save()` always overwrites; admin marks the field readonly. Detection: `test_collection_name_auto_populated_on_save` already covers it."

**L1.5 — [minor] Spec pitfall #7 (UniqueConstraint vs unique_together) — confirm spec uses correct form.**
- The spec template uses `models.UniqueConstraint(fields=[...], name="unique_bot_per_tenant")` (modern API), not `unique_together = [...]` (older). Plan inherits correctly. No change needed; flagging for completeness.

**L1.6 — [minor] Spec pitfall #8 (null=True, blank=True on optional URL/filename) not in risk register.**
- Document.source_filename and Document.source_url have `null=True, blank=True`. The implementation must preserve both. If the implementer trims to `blank=True` only (Django anti-pattern claim), the field rejects None.
- How to fix: Risk #19: "Optional Document fields must keep BOTH `null=True, blank=True`. Spec mandates."

**L1.7 — [minor] Spec pitfall #9 (auto_now_add vs auto_now) not in risk register.**
- `uploaded_at = DateTimeField(auto_now_add=True)` — set once at creation.
- `last_refreshed_at = DateTimeField(auto_now=True)` — updates on every save.
- Mixing them up means timestamps lie.
- How to fix: Risk #20: short note. Detection: not directly tested but the spec snippet is verbatim.

**L1.8 — [minor] §7's "12 files" reconciliation.**
The 01_plan prompt parenthesises "(the 12 files in spec.md hard constraint #1)". Spec actually lists 15 items. Plan §7 already calls this out and uses the 15-file list. No change needed; minor note.

### Lens 2 — Missed edge cases

**L2.1 — [minor] Plan doesn't make explicit that `makemigrations` does NOT connect to a database.**
The implementer might assume `makemigrations` requires Compose-up, leading to wasted effort. `manage.py check` and `makemigrations` are pure Python introspection; only `migrate` connects.
- How to fix: §9 cheat-sheet note: "`makemigrations` and `manage.py check` do not connect to the DB; they work without `docker compose up`. Only `migrate` needs a live database."

**L2.2 — [minor] Grep regex `r'f"t_.*__b_'` catches f-strings only.**
False negatives: `"t_" + tenant_id + "__b_" + bot_id` (concatenation), `"t_{}__b_{}".format(...)`, `"t_" + ...` etc. Spec accepts the f-string-only check; the test guards the *common* failure mode, not the *theoretical* one.
- How to fix: §6 ambiguity #11: "Grep guard regex catches f-strings only. False negatives possible for concatenation/.format() forms. Mitigation: code reviews; the helper should be the only call site for collection-name construction. Risk is low because future contributors will follow the spec's pattern."

**L2.3 — [minor] pytest-django auto-migrate semantics for SQLite.**
pytest-django runs all migrations against the SQLite test DB on session start. Tenant/Bot/Document use only portable types (CharField, TextField, IntegerField, UUIDField, DateTimeField, FK), so SQLite compatibility is expected. Already covered by Risk #14, but the plan should mention that pytest's verification of SQLite migrate is implicit, not explicit.
- How to fix: Add note to Step 9 / Checkpoint I that pytest-django re-applies all migrations as part of its setup.

**L2.4 — [minor] Document creation without explicit tenant_id/bot_id.**
Spec accepts `Document.objects.create(bot=b, source_type='pdf', content_hash=...)` — `tenant_id` and `bot_id` would be empty strings or null. Currently the spec doesn't add a `clean()` or `save()` override. Already in §6 ambiguity #4. No change.

**L2.5 — [minor] Bot re-save with `force_insert=True` on an already-saved Bot recomputes collection_name and tries to re-INSERT.**
This would fail on the unique constraint. Acceptable: re-INSERT of an existing Bot is an error. Already in Risk #13.

**L2.6 — [minor] grep guard test runs from tests/ but the plan rewrites it to use `pathlib.Path(__file__).resolve().parent.parent`.**
The spec template uses the same pattern (relative to test file). The pure-Python rewrite at Step 3 must preserve this — the repo root is two levels up from `tests/test_naming.py`.
- How to fix: Step 3 instructions are clear; just verify the implementer uses `pathlib.Path(__file__).resolve().parents[1]` (or `.parent.parent`).

**L2.7 — [minor] Step 11's admin walkthrough requires JavaScript-enabled browser.**
The spec walkthrough is a manual browser action. Curl-based verification of admin login is in the implement prompt (`curl /admin/login/`), but creating a Tenant via curl requires CSRF token handling — out of scope for this verification. Manual walkthrough is acceptable per spec.

### Lens 3 — Production-readiness gaps

**L3.1 — [minor] `source_filename = CharField(null=True, blank=True)` is a Django anti-pattern (typically prefer empty string).**
Spec mandates this form. The plan inherits correctly. The semantic justification: a PDF upload genuinely has "no URL" and a URL scrape genuinely has "no filename" — null is more honest than empty string. Acceptable. Document the convention in §6 ambiguity #12.

**L3.2 — [minor] Document.error_message is `TextField(null=True, blank=True)` and could hold a full stack trace.**
JSON serialization to logs (Phase 5/8) needs to consider truncation. Out of scope for Phase 2 but worth noting.
- How to fix: Risk #21: "Document.error_message can hold large strings; Phase 5/8 must truncate before logging."

**L3.3 — [minor] CASCADE delete is operationally destructive.**
Tenant.delete() cascades through every Bot and every Document. Production should require a confirmation step (admin's default delete page asks once). Spec defers soft-delete to v3. Already in Risk #9. Add an operational note.

**L3.4 — [minor] mypy state stays at Phase 1's PARTIAL.**
Phase 1 implementation_report flagged 2 missing-stub warnings (celery, environ) and recommended a Phase 2 `[tool.mypy]` config. Spec doesn't mandate it; pyproject.toml is on the don't-touch list. Defer to a future phase.
- How to fix: §6 ambiguity #13: "mypy config deferred. Phase 2 cannot touch pyproject.toml."

### Lens 4 — Pitfall coverage audit

**L4.1 — [minor] Pitfalls #5, #7, #8, #9 not in plan's risk register.**
Already captured in L1.4–L1.7. Add risks #18–#20.

**L4.2 — Pitfall #1 (circular imports).** Covered by Risk #2 + Ambiguity #2 + Step 1/2 import smokes. Verification commands actually catch the failure (ImportError surfaces at import time). ✓

**L4.3 — Pitfall #2 (Bot.save() not populating).** Risk #3 + `test_collection_name_auto_populated_on_save`. ✓

**L4.4 — Pitfall #3 (`@pytest.mark.django_db` missing).** Risk #4 + class-level decorator on each test class. Verification: pytest output would show "Database access not allowed" if the marker is missed. ✓

**L4.5 — Pitfall #4 (premature migration commits).** Risk #6 + Checkpoint F file-list inspection. ✓

**L4.6 — Pitfall #6 (Document denorm drift).** Ambiguity #4 (trust Phase 5). ✓

**L4.7 — Pitfall #10 (Bot._meta.get_field AppRegistryNotReady).** Ambiguity #8 — pytest-django auto-handles django.setup. ✓

### Lens 5 — Sequencing & dependency correctness

No findings. Walking the plan's 11 build steps:
- Each step's dependencies are earlier in the sequence.
- No step requires output from a later step.
- After every step the working tree is in a coherent state (validators.py alone is fine; naming.py alone is fine; etc.).
- Step 6 (admin) and Step 7 (makemigrations) are both downstream of Step 4 (models); their order between each other is not load-bearing — admin doesn't affect schema, and makemigrations doesn't read admin. The plan's choice (admin → migrations) keeps the file-creation order parallel to the `apps/<x>/{models,admin}.py` pattern.

### Lens 6 — Verification command quality

**L6.1 — [major] Checkpoint F's grep-based dependency check is fragile.**
Current command:
```bash
grep -E "^\s+(initial|operations|dependencies)" apps/tenants/migrations/0001_initial.py apps/documents/migrations/0001_initial.py | head -40
```
This greps for keywords, not the actual dependency declaration. A migration with `dependencies = []` would still match and pass. The plan's intent — "verify documents.0001 declares tenants.0001 as a dependency" — is not actually verified.
- How to fix: Replace with Python introspection:
  ```bash
  uv run python -c "
  from apps.tenants.migrations import __init__ as _t
  from apps.documents.migrations import __init__ as _d
  from importlib import import_module
  m_t = import_module('apps.tenants.migrations.0001_initial').Migration
  m_d = import_module('apps.documents.migrations.0001_initial').Migration
  print('tenants.deps:', m_t.dependencies)
  print('documents.deps:', m_d.dependencies)
  assert any('tenants' in str(d) for d in m_d.dependencies), 'documents migration missing tenants dep'
  print('dep check OK')
  "
  ```
  Note: the migration filename starts with a digit (`0001_initial`) so `import_module('apps.tenants.migrations.0001_initial')` is illegal Python syntax (digits can't start a module name in `from x import y`, but `import_module(string)` accepts arbitrary strings). The `import_module` form is correct.
- Severity: Major because a passing-but-meaningless verification creates false confidence.

**L6.2 — [minor] Strengthen Checkpoint A and B with negative-case checks.**
Current Checkpoint A only verifies happy-path. Add:
```bash
uv run python -c "from apps.tenants.validators import validate_slug, InvalidIdentifierError; \
  validate_slug('pizzapalace'); \
  try: validate_slug('Pizza'); raise SystemExit('FAIL: Pizza should be rejected') \
  except InvalidIdentifierError: print('reject ok')"
```
- Severity: Minor — Step 3's pytest covers this thoroughly; the smoke is just a quick sanity check.

**L6.3 — [minor] Checkpoint H should verify django_migrations table.**
Add to Checkpoint H:
```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT app, name FROM django_migrations WHERE app IN ('tenants','documents') ORDER BY id;"
```
Expected: rows for `tenants 0001_initial` and `documents 0001_initial`.

**L6.4 — [minor] Checkpoint E (manage.py check after admin) is redundant with Checkpoint D (manage.py check after models).**
Admin doesn't change schema. Combining: drop Checkpoint E entirely or downgrade to "(implicit — same as D)."

**L6.5 — [minor] Checkpoint K's admin walkthrough is browser-based and not scripted.**
Acceptable. But the implementation prompt has a `curl /admin/login/ | grep -c "Django administration"` quick check that should also be run at Checkpoint K alongside the manual walkthrough.

### Lens 7 — Tooling correctness

**L7.1 — [major] HTTP_PORT discrepancy: plan uses `${HTTP_PORT}` but local .env has 8080, while spec.md acceptance criterion 9 says port 8080.**
The local `.env` has `HTTP_PORT=8080`. Phase 1 docker-compose.yml maps `${HTTP_PORT:-8000}:8000`. Spec.md acceptance criterion 9 is `curl -fsS http://localhost:8080/healthz`. The 03_implement prompt also uses 8080. The plan should consistently use 8080 (or `${HTTP_PORT:-8080}` to match what's actually on the user's system).
- How to fix: Update Step 11 + cheat-sheet to `${HTTP_PORT:-8080}` or just `8080`.
- Severity: Major because copy-paste of plan commands would target 8000 and fail.

**L7.2 — [minor] `make migrations tenants documents` (scoped) vs bare.**
Plan correctly chooses scoped. Note: the bare form also works because Phase 2 only adds models to `tenants` and `documents` (no other app gets new models), but the scoped form is defensive against accidental other-app pollution. Plan addresses this in §9 already. ✓

**L7.3 — [minor] `migrate` against dev override stack uses `runserver`, not gunicorn — but `migrate` runs in either case.**
Both `docker-compose.yml` (production-mode) and `docker-compose.override.yml` (dev) have the web container's command include `python manage.py migrate --noinput` before launching the server. Either path applies migrations. Plan addresses in §9.

**L7.4 — [minor] `createsuperuser --noinput` requires DJANGO_SUPERUSER_USERNAME, _EMAIL, _PASSWORD.**
Plan §9 addresses correctly. Implementation prompt's example omits `DJANGO_SUPERUSER_USERNAME` and `_PASSWORD` — implementer should set them or use the interactive form.

**L7.5 — [minor] `psql -U "$POSTGRES_USER"` in the cheat-sheet assumes the host shell has POSTGRES_USER exported.**
Plan addresses in §9 (`source .env` first). Reinforce.

### Lens 8 — Risk register completeness

**L8.1 — [minor] grep guard test failing on a clean checkout because a Phase 2 file has a `t_..._b_` literal in a docstring.**
Already in Risk #7. The spec template's docstrings don't contain the pattern (verified by re-reading the spec). ✓

**L8.2 — [minor] Web container's `migrate` startup hits new tables before code references them.**
Phase 2 doesn't have any code that queries Tenant/Bot/Document yet (no views, no admin until /admin/ is browsed). Healthz only pings DB connectivity, not specific tables. ✓ irrelevant.

**L8.3 — [minor] Admin login redirect after auth.**
Default `LOGIN_REDIRECT_URL` is `/accounts/profile/`, but Django admin uses its own `next` param after login (defaults to `/admin/`). Phase 1 settings.py doesn't override `LOGIN_REDIRECT_URL`. Spec walkthrough at `/admin/` works regardless.

**L8.4 — [minor] Test runtime increase from N tests to N+~20 tests.**
Each model test rolls back a transaction (~5–20ms). Total test runtime grows by < 1s. Acceptable.

**L8.5 — [minor] Host-side Docker blockers (Phase 1 implementation_report.md).**
Per memory `project_phase_1_lessons.md`: don't re-quiz user about docker group / port conflicts. Assume resolved unless implementation observes failures.
- How to fix: Cheat-sheet note: "If `docker compose` permission-denies, see Phase 1 implementation_report.md outstanding-issues block. Otherwise assume resolved."

---

## Findings escalated to user

**No new findings require user input before Prompt 3 can run.**

The original plan's §6 ambiguity #1 (Document FK/CharField column collision) is already escalated correctly: Checkpoint D's introspection step will surface it pre-migration, with proposed resolutions (`db_column='bot_pk'` on the FK, or rename CharField to `bot_slug`/`tenant_slug`). That escalation pattern is preserved in the revision.

If Checkpoint D fires the duplicate-columns assertion during Prompt 3, the implementer must STOP and surface the failure to the user before generating migrations. The plan's instructions are clear on this; the revision adds a slightly stronger introspection one-liner.

---

## Recommendation

**Ready for Prompt 3.** All findings are mechanical (verification strength, port number, missing pitfall mentions) and resolved inline in the revised plan.md. No architectural decisions are deferred to user input. The revised plan adds a §0 "Revision notes" cross-referencing this review's finding IDs, augments the risk register from 17 to 21 entries (covering pitfalls #5, #7, #8, #9 and the operational notes), strengthens Checkpoint F's verification, and aligns HTTP_PORT to 8080.
