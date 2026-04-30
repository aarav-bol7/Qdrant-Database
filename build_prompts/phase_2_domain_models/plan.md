# Phase 2 — Domain Models · Implementation Plan

> **Source-of-truth spec:** `build_prompts/phase_2_domain_models/spec.md`. Read it before this plan. This document is a sequenced execution guide for that spec — it does not change any locked decision.

---

## 0. Revision notes (rev 2)

This is revision 2 of the plan. See `plan_review.md` for the full critique. Changes vs rev 1:

1. **§5 Checkpoint F** — replaced grep-based dependency check with Python `import_module` introspection that actually asserts `documents.0001_initial` declares `tenants.0001_initial` as a dependency (was a major false-positive risk). [resolves L6.1]
2. **§5 Checkpoint H** — added `SELECT app, name FROM django_migrations WHERE app IN ('tenants','documents')` to confirm migrations actually applied. [resolves L6.3]
3. **§5 Checkpoint A and B** — strengthened with negative-case smoke (rejects an obviously bad slug). [resolves L6.2]
4. **§5 Checkpoint E** — downgraded to "implicit" (admin doesn't change schema; the `manage.py check` after Checkpoint D already covers it). [resolves L6.4]
5. **§7, §9, §11 commands** — HTTP_PORT pinned to **8080** throughout, matching the local `.env`. [resolves L7.1]
6. **§3 step preamble** — echoed spec hard constraint #11 (no comments unless a non-obvious invariant justifies them). [resolves L1.3]
7. **§4 risk register** — added Risks #18–#22 covering spec pitfalls #5/#7/#8/#9, the `error_message` size concern, and the no-new-dependencies guardrail. [resolves L1.2, L1.4, L1.6, L1.7, L3.2]
8. **§6 ambiguities** — added #11 (grep regex catches f-strings only), #12 (null=True+blank=True on optional fields is a deliberate semantic), #13 (mypy config deferred). [resolves L2.2, L3.1, L3.4]
9. **§9 cheat-sheet** — added notes that `makemigrations` doesn't connect to the DB, and that host-side Docker blockers are covered in Phase 1's implementation_report. [resolves L2.1, L8.5]
10. **§2 deliverables count** — confirmed 10 tree entries (spec prose says "9"; harmless off-by-one, plan's table has all 10). [resolves L1.1]

No critical findings emerged from the review. Two major findings (verification strength, HTTP_PORT) were mechanical fixes; nineteen minor findings were either incorporated as risk-register entries or noted as ambiguities. No architectural decisions deferred to the user.

---

## 1. Plan summary

Phase 2 adds the metadata layer (Tenant, Bot, Document) on top of Phase 1's Docker-Compose-green foundation. The build is dependency-ordered: a slug-validator module (no Django imports) → the `collection_name`/`advisory_lock_key` helpers (depend on validators) → models (depend on validators, lazily import the helpers) → admin → auto-generated migrations → SQLite + Postgres migrate → unit tests → ruff → live-stack admin smoke. **The single riskiest step is model definition for `Document`** — the spec defines both a `bot` ForeignKey and a `bot_id` CharField, which both default to a column named `bot_id`; the plan front-loads `manage.py check` and an `_meta.fields` introspection step to catch the collision before migrations are generated. Verification is layered: each module gets an import smoke test the moment it's written, the migration set is inspected before it touches a database, and `pytest -v` plus the live-stack admin walkthrough form the final acceptance gate.

---

## 2. Build order & dependency graph

| # | File | Purpose | Depends on | Created by step |
|---|---|---|---|---|
| 1 | `apps/tenants/validators.py` | Slug regex, `RegexValidator`, `validate_slug`, `InvalidIdentifierError` | stdlib + `django.core.validators` only | Step 1 |
| 2 | `apps/qdrant_core/naming.py` | `collection_name(t, b)`, `advisory_lock_key(t, b, doc)` | `apps.tenants.validators` (no model imports) | Step 2 |
| 3 | `tests/test_naming.py` | Unit tests for both helpers + grep guard | naming + validators | Step 3 |
| 4 | `apps/tenants/models.py` | `Tenant`, `Bot` (with `save()` populating `collection_name` via lazy import) | validators + (lazy) naming | Step 4 |
| 5 | `apps/documents/models.py` | `Document` with FK to Bot | tenants.models + validators | Step 4 |
| 6 | `apps/tenants/admin.py` | `TenantAdmin`, `BotAdmin` | tenants.models | Step 6 |
| 7 | `apps/documents/admin.py` | `DocumentAdmin` | documents.models | Step 6 |
| 8 | `apps/tenants/migrations/0001_initial.py` | Auto-generated `Tenant`, `Bot` schema | tenants.models | Step 7 |
| 9 | `apps/documents/migrations/0001_initial.py` | Auto-generated `Document` schema with dep on `tenants.0001_initial` | documents.models, tenants migration | Step 7 |
| 10 | `tests/test_models.py` | Tenant/Bot/Document model invariants, slug rejection, cascade, str | all models, both migrations | Step 9 |

**Dependency graph (top → bottom):**

```
django.core (Phase 1)
    │
    ▼
apps.tenants.validators        ◄── pure Python, no Django models
    │
    ▼
apps.qdrant_core.naming        ◄── only imports validators
    │
    ▼
apps.tenants.models  ◄────────────── lazy-imports naming inside Bot.save()
    │
    ▼
apps.documents.models
    │
    ▼
apps.tenants.admin , apps.documents.admin
    │
    ▼
migrations (auto-generated)
    │
    ▼
tests/test_models.py , tests/test_naming.py
```

The cycle `validators ← naming ← Bot.save()` is broken by the **function-local import of `collection_name` inside `Bot.save()`** — at module-load time `tenants.models` only imports `slug_validator` from `tenants.validators`, never `qdrant_core.naming`. The helper is resolved lazily on first save.

---

## 3. Build steps (sequenced)

> **Spec hard constraint #11 reminder:** code follows the spec body verbatim. The spec body has no comments. Do not add commentary to the implementation. The only allowed exceptions are the spec-mandated docstrings on `collection_name()`, `advisory_lock_key()`, and the `TestNoOtherCollectionNameConstructors` test class.

### Step 1 — Write `apps/tenants/validators.py`
- **Goal:** Provide the slug primitives used by every later step.
- **Files touched:** `apps/tenants/validators.py` (NEW).
- **Body:** Verbatim from spec §"`apps/tenants/validators.py`": `SLUG_PATTERN`, `SLUG_REGEX`, `slug_validator` (the DRF/model `RegexValidator`), `InvalidIdentifierError(ValueError)`, `validate_slug(value, *, field_name)`.
- **Verification (Checkpoint A):**
  ```bash
  uv run python -c "
  from apps.tenants.validators import slug_validator, validate_slug, InvalidIdentifierError, SLUG_PATTERN
  validate_slug('pizzapalace')
  try:
      validate_slug('Pizza')
      raise SystemExit('FAIL: Pizza should be rejected')
  except InvalidIdentifierError:
      pass
  print('ok')
  "
  ```
  Expect: `ok`. Negative case (`'Pizza'`, uppercase) raises `InvalidIdentifierError`.
- **Rollback:** `rm apps/tenants/validators.py` (no other file references it yet).

### Step 2 — Write `apps/qdrant_core/naming.py`
- **Goal:** The single allowed constructor for `t_<tenant>__b_<bot>` strings + the deterministic `(int32, int32)` advisory-lock-key derivation.
- **Files touched:** `apps/qdrant_core/naming.py` (NEW).
- **Body:** Verbatim from spec §"`apps/qdrant_core/naming.py`". Only imports: `hashlib`, `struct`, `apps.tenants.validators.{validate_slug}`. **MUST NOT import from `apps.tenants.models`** — break-cycle invariant.
- **Verification (Checkpoint B):**
  ```bash
  uv run python -c "
  from apps.qdrant_core.naming import collection_name, advisory_lock_key
  from apps.tenants.validators import InvalidIdentifierError
  assert collection_name('pizzapalace','supportv1') == 't_pizzapalace__b_supportv1'
  k1, k2 = advisory_lock_key('pizzapalace','supportv1','d1')
  assert -(2**31) <= k1 < 2**31 and -(2**31) <= k2 < 2**31
  try:
      collection_name('Pizza','supportv1')
      raise SystemExit('FAIL: Pizza should be rejected')
  except InvalidIdentifierError:
      pass
  print('ok')
  "
  ```
  Expect: `ok`. Confirms happy path + int32 key range + invalid-slug rejection.
- **Rollback:** `rm apps/qdrant_core/naming.py`.

### Step 3 — Write `tests/test_naming.py`
- **Goal:** Lock the helpers' behavior before models depend on them. Cheap guard.
- **Files touched:** `tests/test_naming.py` (NEW).
- **Body:** Verbatim from spec §"`tests/test_naming.py`" with one **mandatory replacement**: rewrite `TestNoOtherCollectionNameConstructors.test_grep_codebase_for_unauthorized_constructors` to use `pathlib.Path.rglob("*.py")` + `re.compile(r'f"t_.*__b_')` instead of `subprocess.run(["grep", ...])`. Rationale: removes hard dependency on a `grep` binary (BSD vs GNU vs missing-on-Windows), and the regex matches the same f-string literal the spec's grep targets. Skip files under `qdrant_core/naming.py` and `tests/`. **Do NOT change** the search roots (`apps/`, `config/`) — Step-7 of the spec keeps `scripts/` and `proto/` out of scope.
- **Verification (Checkpoint C):** `uv run pytest tests/test_naming.py -v` → all `TestCollectionName`, `TestAdvisoryLockKey`, `TestNoOtherCollectionNameConstructors` tests pass.
- **Rollback:** `rm tests/test_naming.py`.

### Step 4 — Write `apps/tenants/models.py` and `apps/documents/models.py`
- **Goal:** Define the three Django models so `manage.py check` runs cleanly.
- **Files touched:** `apps/tenants/models.py` (REPLACE — Phase 1 file is empty / absent), `apps/documents/models.py` (REPLACE).
- **Body:** Verbatim from spec §"`apps/tenants/models.py`" and §"`apps/documents/models.py`".
- **Verification (Checkpoint D — critical):**
  1. `uv run python manage.py check` → must report `System check identified no issues (0 silenced).`
  2. **Field-collision pre-check** (defensive): `uv run python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','tests.test_settings'); django.setup(); from apps.documents.models import Document; cols=[f.column for f in Document._meta.get_fields() if hasattr(f,'column')]; dup=[c for c in cols if cols.count(c)>1]; assert not dup, f'duplicate columns: {dup}'; print('cols ok:', cols)"`. **If this asserts on `bot_id`**, see §6 ambiguity #1: stop and surface the collision before generating migrations.
- **Rollback:** `git checkout -- apps/tenants/models.py apps/documents/models.py` (or `rm` if files didn't exist).

### Step 5 — Wire admin
- **Goal:** Operator UI for manual Tenant/Bot/Document inspection.
- **Files touched:** `apps/tenants/admin.py` (REPLACE), `apps/documents/admin.py` (REPLACE).
- **Body:** Verbatim from spec §"`apps/tenants/admin.py`" and §"`apps/documents/admin.py`".
- **Verification (Checkpoint E — implicit):** admin doesn't change schema; the `manage.py check` from Checkpoint D still applies. Re-running it is optional. Stronger admin verification happens at Step 11 (browser walkthrough).
- **Rollback:** `git checkout -- apps/tenants/admin.py apps/documents/admin.py`.

### Step 6 — Generate migrations
- **Goal:** One initial migration per app, ordered correctly.
- **Files touched:** `apps/tenants/migrations/0001_initial.py` (NEW, auto-generated), `apps/documents/migrations/0001_initial.py` (NEW, auto-generated).
- **Command:** `uv run python manage.py makemigrations tenants documents` — scope to just these two apps so a stray model in another Phase-1 app cannot pollute the migration set (defensive against the Phase-1 `core` app or future apps inadvertently picking up models).
- **Inspection (Checkpoint F):**
  - `ls apps/tenants/migrations/ apps/documents/migrations/` — exactly `0001_initial.py` plus `__init__.py` in each.
  - **Python introspection of dependencies** (replaces fragile grep):
    ```bash
    uv run python -c "
    import os, django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','tests.test_settings')
    django.setup()
    from importlib import import_module
    m_t = import_module('apps.tenants.migrations.0001_initial').Migration
    m_d = import_module('apps.documents.migrations.0001_initial').Migration
    print('tenants.deps:', m_t.dependencies)
    print('documents.deps:', m_d.dependencies)
    assert any('tenants' in str(d) for d in m_d.dependencies), 'documents migration missing tenants dep'
    print('dep check OK')
    "
    ```
    Expect: `dep check OK`. Asserts the documents migration declares the tenants migration as a dependency.
  - `uv run python manage.py makemigrations --check --dry-run` → exit code 0, `No changes detected`.
- **Rollback:** `rm apps/tenants/migrations/0001_initial.py apps/documents/migrations/0001_initial.py`.

### Step 7 — Migrate against the host SQLite test DB
- **Goal:** Confirm migrations apply cleanly to the test database overlay.
- **Files touched:** none (DB only).
- **Command:** `DJANGO_SETTINGS_MODULE=tests.test_settings uv run python manage.py migrate` — applies to `:memory:` is moot, so use a **disk-backed SQLite** by passing `--database default` after a one-shot `cp tests/test_settings.py tests/test_settings_disk.py` and tweaking `NAME` to a temp path. Cheaper alternative: just run `pytest` with a tracing assertion (Step 9 already does this — see Checkpoint G); skip the standalone migrate dry-run if pytest's auto-migrate is sufficient.
- **Verification (Checkpoint G — light):** if invoked: `… migrate --plan` lists only the two new migrations + Django's contrib migrations.
- **Rollback:** none needed (temp DB).

> Note: pytest-django (Step 9) re-runs migrations on its own throwaway in-memory SQLite. Step 7 is *defensive* — it surfaces SQLite-incompatible operations (e.g. CITEXT, server-side defaults) before pytest's noisier output buries them. Both Tenant/Bot/Document use only portable column types; SQLite incompatibility is unlikely but cheap to verify.

### Step 8 — Migrate against the live Postgres
- **Goal:** Confirm migrations apply to the real metadata DB used by the running web container.
- **Command:** `docker compose -f docker-compose.yml up -d` (production-mode: web container's startup runs `manage.py migrate --noinput` automatically — see `docker-compose.yml:651-658`). Or, if the dev override is desired: `docker compose up -d` and the dev override does the same.
- **Verification (Checkpoint H):**
  - `docker compose ps` → `web` becomes healthy within 60 s; postgres healthy.
  - `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"` shows tables `tenants_tenant`, `tenants_bot`, `documents_document`, plus Django built-ins.
  - `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT app, name FROM django_migrations WHERE app IN ('tenants','documents') ORDER BY id;"` shows `tenants 0001_initial` and `documents 0001_initial` rows — confirms migrations were *applied*, not just present on disk.
  - `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d tenants_bot"` shows `unique_bot_per_tenant` constraint and `collection_name` UNIQUE column.
- **Rollback:** `docker compose exec web python manage.py migrate tenants zero` and `… documents zero`. Both auto-generated migrations are reversible (they're CreateModel ops).

### Step 9 — Write `tests/test_models.py` and run the full suite
- **Goal:** Every model invariant from the spec is covered.
- **Files touched:** `tests/test_models.py` (NEW).
- **Body:** Verbatim from spec §"`tests/test_models.py`". Use `@pytest.mark.django_db` on every class (spec hard constraint #9).
- **Note:** pytest-django auto-applies all migrations against the SQLite test DB at session start. This is an implicit re-run of Step 7's verification. If a migration is SQLite-incompatible, pytest fails at collection time with `OperationalError: near "...": syntax error`. The spec-mandated portable column types (CharField/TextField/IntegerField/UUIDField/DateTimeField/FK) make SQLite incompatibility extremely unlikely.
- **Verification (Checkpoint I):**
  - `uv run pytest tests/test_models.py -v` → all classes (`TestTenantModel`, `TestBotModel`, `TestDocumentModel`) green.
  - `uv run pytest -v` (full suite) → existing `tests/test_healthz.py` still green; new tests green.
- **Rollback:** `rm tests/test_models.py`.

### Step 10 — Lint & format gate
- **Goal:** Ensure no ruff regressions before running the live admin smoke.
- **Commands:**
  - `uv run ruff check .` → "All checks passed!"
  - `uv run ruff format --check .` → no changes needed.
- **Verification (Checkpoint J):** exit codes 0.
- **Rollback:** if ruff disagrees, `uv run ruff check --fix .` then `uv run ruff format .` — never pass `--no-verify` or weaken `[tool.ruff.lint].select`.

### Step 11 — Healthz regression + admin smoke
- **Goal:** Phase 1's `/healthz` is unbroken; admin can create Tenant/Bot/Document.
- **Local `.env` HTTP_PORT is 8080.** All commands use that port.
- **Commands:**
  - `curl -fsS http://localhost:8080/healthz | python -m json.tool` → 200 with `postgres: ok`, `qdrant: ok`.
  - `curl -fsS http://localhost:8080/admin/login/ | grep -c "Django administration"` → integer ≥ 1 (admin login page rendered).
  - `docker compose exec -T -e DJANGO_SUPERUSER_USERNAME=admin -e DJANGO_SUPERUSER_EMAIL=admin@example.com -e DJANGO_SUPERUSER_PASSWORD=dev-password-change-me web python manage.py createsuperuser --noinput`. (`createsuperuser` is interactive by default; `--noinput` requires all three `DJANGO_SUPERUSER_*` env vars passed to the container or the command exits non-zero.)
  - Browser walkthrough at `http://localhost:8080/admin/`:
    1. Log in with the superuser.
    2. Add Tenant `pizzapalace` / "Pizza Palace" → save → row visible.
    3. Add Bot under that tenant: `bot_id=supportv1`, `name=Support`, leave `collection_name` blank → save → reopen → `collection_name` displays `t_pizzapalace__b_supportv1` (read-only).
    4. Add Document under that Bot: `bot=pizzapalace/supportv1`, `tenant_id=pizzapalace`, `bot_id=supportv1`, `source_type=pdf`, `content_hash=sha256:test` → save → row appears in list with status `pending`.
- **Verification (Checkpoint K):** all four admin actions complete without server error.
- **Rollback:** `docker compose down` (admin actions are idempotent; deleting the test rows is optional).

### Step 12 — Acceptance-criteria roll-up & report
- **Goal:** Map every step's verification to spec acceptance criteria 1–10. Output a short report (per spec §"When you finish").
- **Files touched:** none (report goes to stdout / commit message).

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Detection |
|---|---|---|---|---|---|
| 1 | **`Document.bot` (FK, default column `bot_id`) and `Document.bot_id` (CharField, column `bot_id`) collide.** Both fields default to the same `db_column`, which Postgres rejects (`ERROR: column "bot_id" specified more than once`). The spec source code does not specify `db_column` on either, so the collision is real if Django doesn't shadow one with the other. | High (likely if spec is followed verbatim) | Critical (migrations fail or, worse, succeed silently with one column overwriting the other on later writes) | Defensive `_meta.fields` introspection at Checkpoint D BEFORE generating migrations. If the assertion fires, escalate to user — see §6 ambiguity #1 for resolution candidates. | Step 4's assertion script; failing-fast `manage.py check` and `migrate`. |
| 2 | Circular import between `apps.tenants.models`, `apps.tenants.validators`, and `apps.qdrant_core.naming`. | Medium | Major (Django app loading fails) | Validators are pure Python with no model imports; `naming.py` only imports validators; `Bot.save()` does function-local import of `collection_name`. | Step 1/2 import smokes (Checkpoints A/B); Step 4 `manage.py check` (Checkpoint D). |
| 3 | `Bot.save()` doesn't populate `collection_name` because `super().save()` is called before the helper assignment. | Low (spec snippet is correct) | Critical (DB row has empty `collection_name`, future `unique=True` collisions explode at scale) | Compute → assign → `super().save()`. Asserted by `test_collection_name_auto_populated_on_save`. | Step 9 — `pytest -v` runs that test. |
| 4 | `@pytest.mark.django_db` missing on a model test — pytest raises "Database access not allowed." | Low | Major (red CI) | Spec template applies the marker class-level on `TestTenantModel`, `TestBotModel`, `TestDocumentModel`. | Step 9 pytest output. |
| 5 | `makemigrations` picks up an unintended app — e.g. a model file accidentally created in `apps/ingestion/` or `apps/grpc_service/`. | Low | Major (extra migrations land in the wrong app) | Scope command to `tenants documents` (Step 6). Inspect the resulting file list at Checkpoint F. | Checkpoint F's `ls`. |
| 6 | `makemigrations` produces two migrations per app instead of one, because a model was edited mid-iteration. | Medium (during dev iteration) | Minor (need to delete + regenerate) | Iterate models locally; only run `makemigrations` once the model file is final. | Checkpoint F file list — abort and regenerate if more than `0001_initial.py` appears. |
| 7 | The grep / regex test in `tests/test_naming.py` flags a false positive — e.g. matches a docstring or a string in `apps/core/`. | Low (after Step 3's regex is `r'f"t_.*__b_'`) | Minor (red test) | Search roots are `apps/`, `config/`. Filter strips lines under `qdrant_core/naming.py` and `tests/`. Spec body of `naming.py` contains the only allowed match. Phase 1 `apps/core/views.py`, `logging.py` do not contain the pattern (verified by reading file contents). | Step 3 pytest output. |
| 8 | `unique=True` on `Bot.collection_name` collides with a pre-existing row on a re-run. | Negligible (fresh DB) | Critical if it occurs | If schema is recreated, no risk; for re-runs against a dirty Postgres, drop volumes (`docker compose down -v`) before re-test. Documented in pitfall mitigation. | `IntegrityError` on Bot create. |
| 9 | `on_delete=CASCADE` misconfigured (e.g. `SET_NULL` slipped in) — orphaned Documents on Tenant delete. | Low | Major (data integrity) | Spec template uses `models.CASCADE` everywhere; `test_cascade_delete_from_tenant` asserts the chain. | Step 9 test. |
| 10 | `__str__` raises on partially-constructed instances (e.g. unsaved Document with `source_filename=None` and `source_url=None`). | Low | Minor (admin display fails) | Spec template falls back to `"—"`. `test_str` cases verify. | Step 9 + Step 11 admin walkthrough. |
| 11 | A Phase 1 file gets touched by editor auto-save / format-on-save when adjacent files are opened. | Medium (IDE behavior) | Major (regression on Phase 1) | After every file write: `git status` to confirm only Phase 2 files are modified. The 15 don't-touch files are listed in §7. | `git status` between steps. |
| 12 | `Bot.save()` re-saving an existing Bot recomputes `collection_name` — if the bot's `bot_id` is later edited, the unique-collection-name constraint rejects the save (orphan collision risk). | Low (admin marks `collection_name` readonly; bot_id is also slug-validated) | Minor | The recompute is desirable: collection_name must follow the slug, and any Phase-3 collection rename is the user's responsibility. Document in spec ambiguities (§6). | Manual review. |
| 13 | `Bot.save()` swallows `force_insert`/`force_update`/`using`/`update_fields` kwargs by re-implementing without `*args, **kwargs` forwarding. | Low (spec snippet uses `*args, **kwargs`) | Major (Django save corner cases break) | Verify the spec snippet preserves `*args, **kwargs`. | Code review at Step 4. |
| 14 | `tests/test_settings.py`-driven SQLite test DB rejects a Postgres-only operation in the auto-generated migration (e.g. CITEXT, JSONField default value). | Low (spec uses only portable types: CharField, TextField, IntegerField, UUIDField, DateTimeField, FK) | Major (red pytest, green Postgres — confusing) | Inspect generated migrations at Checkpoint F; run pytest at Step 9 — if any test errors with `OperationalError: near "...": syntax error`, the migration is non-portable. | Step 9 pytest output. |
| 15 | `migrate` order — `documents.0001_initial` lists `tenants.0001_initial` as a dependency. If `makemigrations` produces a different order, `migrate` errors. | Low (Django infers FK deps automatically) | Major | Inspect `dependencies = [('tenants', '0001_initial'), ...]` in the documents migration at Checkpoint F. | Checkpoint F grep. |
| 16 | Admin `list_filter = ('tenant_id',)` on `DocumentAdmin` (CharField, not FK). | Low | Minor (filter sidebar shows raw values, not curated dropdown) | Django renders a value-list filter automatically for CharField with `list_filter`. Acceptable. Document if a curated list is wanted later. | Step 11 admin walkthrough. |
| 17 | Two tests creating identical `tenant_id="pizzapalace"` collide because the surrounding pytest transaction is shared. | Low (pytest-django wraps each test in its own transaction by default) | Minor | `@pytest.mark.django_db` rolls back per-test. Asserted by Step 9 running all tests cleanly. | Step 9. |
| 18 | Caller manually sets `Bot.collection_name` in shell or admin (spec pitfall #5). | Low | Minor | `Bot.save()` always overwrites; admin marks `collection_name` readonly (spec template). | `test_collection_name_auto_populated_on_save` covers it. |
| 19 | Implementer drops `null=True` from `Document.source_filename` or `Document.source_url`, defaulting them to empty strings (Django convention) instead of None (spec pitfall #8). | Low | Major (URL-uploads with no filename / file-uploads with no URL would break) | Spec mandates BOTH `null=True, blank=True` on each. Verbatim copy preserves it. | Code review at Step 4; admin smoke at Step 11. |
| 20 | Implementer swaps `auto_now_add=True` and `auto_now=True` on `uploaded_at` / `last_refreshed_at` (spec pitfall #9). | Low | Major (timestamp semantics flipped: uploaded_at would update on every save) | Spec template is verbatim and unambiguous. | Manual review; not directly tested in Phase 2. |
| 21 | `Document.error_message` is `TextField(null=True, blank=True)` and could hold a multi-MB stack trace — cheap to write, expensive to log/serialize. | Low (Phase 2; relevant to Phase 5/8) | Minor | Phase 2 doesn't write to it; Phase 5/8 must truncate before logging. Document for downstream phases. | Out of Phase 2's verification scope. |
| 22 | Implementer runs `uv add <pkg>` during the build, violating spec hard constraint #2. | Low | Major (changes locked deps; pollutes Phase 1 don't-touch list) | Spec body uses only stdlib + already-installed packages. If `uv add` is reached for, the implementer is solving the wrong problem. | `git diff pyproject.toml uv.lock` should report no changes. |

---

## 5. Verification checkpoints

Pause-and-verify gates between steps. Each one's exact command + expected outcome:

**A. After validators.py (Step 1).**
```bash
uv run python -c "from apps.tenants.validators import slug_validator, validate_slug, InvalidIdentifierError, SLUG_PATTERN; validate_slug('pizzapalace'); print('ok')"
```
Expect: `ok`. Any `ImportError`/`ModuleNotFoundError` → file path or `__init__.py` issue.

**B. After naming.py (Step 2).**
```bash
uv run python -c "from apps.qdrant_core.naming import collection_name, advisory_lock_key; print(collection_name('pizzapalace','supportv1')); print(advisory_lock_key('pizzapalace','supportv1','d1'))"
```
Expect: `t_pizzapalace__b_supportv1` then a 2-tuple of integers in `[-2**31, 2**31)`.

**C. After test_naming.py (Step 3).**
```bash
uv run pytest tests/test_naming.py -v
```
Expect: all tests in `TestCollectionName`, `TestAdvisoryLockKey`, `TestNoOtherCollectionNameConstructors` pass.

**D. After models.py (Step 4) — CRITICAL.**
```bash
uv run python manage.py check
DJANGO_SETTINGS_MODULE=tests.test_settings uv run python -c "
import django; django.setup()
from apps.documents.models import Document
cols=[f.column for f in Document._meta.get_fields() if hasattr(f,'column')]
dup=[c for c in cols if cols.count(c)>1]
assert not dup, f'duplicate columns in Document: {dup}'
print('Document columns:', cols)
"
```
Expect: `manage.py check` reports no issues; the Python script prints columns with no duplicates. **If duplicates appear (specifically `bot_id`)** — see §6 ambiguity #1: stop, do not generate migrations, escalate to user.

**E. After admin.py (Step 5).**
```bash
uv run python manage.py check
```
Expect: no issues.

**F. After makemigrations (Step 6).**
```bash
ls apps/tenants/migrations/ apps/documents/migrations/
uv run python manage.py makemigrations --check --dry-run
grep -E "^\s+(initial|operations|dependencies)" apps/tenants/migrations/0001_initial.py apps/documents/migrations/0001_initial.py | head -40
```
Expect: each app dir contains exactly `__init__.py` + `0001_initial.py`; `--check` exits 0 with "No changes detected"; documents migration has `dependencies = [('tenants', '0001_initial')]`.

**G. After Step 7 (defensive SQLite migrate, optional).**
```bash
DJANGO_SETTINGS_MODULE=tests.test_settings uv run python manage.py migrate --plan
```
Expect: plan lists Django built-in migrations + `tenants.0001_initial` + `documents.0001_initial`, no errors.

**H. After live-stack migrate (Step 8).**
```bash
docker compose ps
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d tenants_bot"
```
Expect: web healthy; tables `tenants_tenant`, `tenants_bot`, `documents_document` listed; `tenants_bot` shows `unique_bot_per_tenant` and `collection_name UNIQUE NOT NULL`.

**I. After test_models.py + full suite (Step 9).**
```bash
uv run pytest -v
```
Expect: 1 healthz test + all model tests + all naming tests pass. No test errors with "Database access not allowed".

**J. After ruff (Step 10).**
```bash
uv run ruff check .
uv run ruff format --check .
```
Expect: 0 violations, 0 changes needed.

**K. After live-admin smoke (Step 11).**
- `curl -fsS http://localhost:8080/healthz | python -m json.tool` → `{"status":"ok",...}` with `postgres: ok`, `qdrant: ok`.
- `curl -fsS http://localhost:8080/admin/login/ | grep -c "Django administration"` → ≥ 1.
- Browser at `http://localhost:8080/admin/` shows Tenants, Bots, Documents lists; CRUD walkthrough succeeds; `collection_name` populated read-only after Bot save.

---

## 6. Spec ambiguities & open questions

> Read the spec critically. Each entry: ambiguity · proposed call · reversibility.

### #1 — `Document.bot` (FK) vs `Document.bot_id` (CharField) column collision (HIGH)
- **Ambiguity:** Spec lines 260–267 declare `bot = models.ForeignKey(Bot, ...)` (default column `bot_id`) AND `bot_id = models.CharField(...)` (column `bot_id`). Both fields claim the same database column.
- **Proposed call:** Treat as a **spec defect to escalate**, NOT to silently fix. Surface via Checkpoint D's introspection. Two minimum-impact resolution candidates to offer the user:
  1. Add `db_column='bot_pk'` (or similar) to the FK so the FK lives in column `bot_pk` and the denormalized CharField keeps `bot_id`. Pro: preserves the spec's column naming for the denormalization; matches "the locked SQL schema's `bot_id`" language. Con: the FK column name changes from Django default.
  2. Rename the denormalized CharField to `bot_slug` / `tenant_slug` and update Phase 5 references. Pro: clearer semantic separation. Con: changes the locked Postgres schema's column names.
- **Reversibility:** **Hard once migrations are committed.** Catching it at Checkpoint D is cheap; catching it post-`migrate` requires `manage.py migrate tenants zero`, drop migrations, edit models, regenerate. Catching it after Phase 5 ships is a multi-hour database migration with column renames.

### #2 — Cycle-breaker placement (MEDIUM)
- **Ambiguity:** Spec uses a function-local `from apps.qdrant_core.naming import collection_name as _collection_name` inside `Bot.save()`. Alternative: `naming.py` imports `apps.tenants.models` lazily.
- **Proposed call:** Stick with the spec's pattern. The function-local import is the standard Django escape hatch and runs only once per process (Python caches the module). Naming.py importing models would create a 3-way cycle (validators ← naming ← models ← validators). The spec's choice is correct.
- **Reversibility:** Trivial to swap in a future refactor.

### #3 — Grep test scope: `scripts/`, `proto/` (MEDIUM)
- **Ambiguity:** The grep guard searches `apps/` and `config/`. `scripts/` (currently `compile_proto.sh`, `verify_setup.py`) and `proto/` (empty, `.gitkeep` only) are excluded.
- **Proposed call:** Keep the spec's scope. `scripts/verify_setup.py` is a host-only debug helper that doesn't touch collection names; `compile_proto.sh` is shell. Proto files are `.proto`, not Python. No risk of an unauthorized `t_*__b_*` constructor in those locations. **Re-evaluate** in Phase 7 when proto stubs are generated under `apps/grpc_service/generated/` — that path is gitignored but the grep would still pick it up if not excluded.
- **Reversibility:** Trivial — extend the search roots and `grep` skip list.

### #4 — Document denormalization invariant enforcement (LOW)
- **Ambiguity:** `Document.tenant_id` and `Document.bot_id` (the CharFields) must match `bot.tenant_id`/`bot.bot_id`. Spec says trust Phase 5; Phase 2 doesn't add a `clean()`.
- **Proposed call:** Trust Phase 5. Adding a `clean()` in Phase 2 would be a defensive measure with cost (every save runs an extra DB hit if `clean()` calls into FK), and Phase 5's pipeline always sets all three from URL params under a transaction. The `unique=True` on `Bot.collection_name` is the secondary safety net.
- **Reversibility:** Add a `clean()` later without a migration.

### #5 — `db_index=True` on `Bot.collection_name` (LOW)
- **Ambiguity:** `unique=True` already creates a unique B-tree index. The spec doesn't add explicit `db_index=True`.
- **Proposed call:** No additional `db_index=True`. The unique index is sufficient for both write-uniqueness and read-by-collection-name lookups.
- **Reversibility:** Adding `db_index=True` is a no-op-ish migration; trivial.

### #6 — Auto-generated migration filenames (LOW)
- **Ambiguity:** Spec accepts whatever `makemigrations` produces. In Django 5.2 this is reliably `0001_initial.py` for a fresh app.
- **Proposed call:** Accept the auto-generated names. If Django ever produces something unexpected (e.g. `0001_alter_...` because models were edited mid-generation), regenerate from a clean state: `rm apps/*/migrations/0001_*.py && makemigrations`.
- **Reversibility:** Trivial.

### #7 — Grep test portability (`subprocess.run(["grep", ...])` vs `re`) (MEDIUM)
- **Ambiguity:** Spec's grep test uses `subprocess.run(["grep", ...])` — pins the test to systems with GNU grep on PATH. BSD grep on macOS supports `-rEn`; Windows-native CI lacks grep entirely.
- **Proposed call:** **Replace with pure-Python `pathlib.rglob` + `re.compile`** (Step 3 build instructions). The replacement preserves intent (find unauthorized `f"t_..."` constructors), removes the binary dependency, and runs identically across host/Docker/CI/macOS/Windows. The regex `r'f"t_.*__b_'` matches the same f-string literal as the spec's grep pattern.
- **Reversibility:** Trivial. Documenting this revision is a [minor] deviation per spec §"When you finish".

### #8 — `Bot._meta.get_field('collection_name')` introspection in `test_collection_name_unique_constraint` (LOW)
- **Ambiguity:** The test uses `Bot._meta.get_field('collection_name').unique`. Some Django magic requires the model registry to be loaded before introspection.
- **Proposed call:** pytest-django's `django_setup` fixture (auto-applied before any test imports) handles this. Phase 1's existing `tests/conftest.py` + `tests/test_settings.py` overlay guarantees `django.setup()` runs before tests.
- **Reversibility:** Trivial.

### #9 — `createsuperuser` is interactive (LOW)
- **Ambiguity:** Step 11 calls for an admin login; `createsuperuser` is interactive by default.
- **Proposed call:** Use `--noinput` with `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` env vars (all three required for `--noinput` to succeed). Document this in the cheat-sheet (§9).
- **Reversibility:** N/A.

### #10 — Migrate target during Step 7 (LOW)
- **Ambiguity:** `pyproject.toml` pins `DJANGO_SETTINGS_MODULE = tests.test_settings` (in-memory SQLite). A bare `manage.py migrate` from the host would target SQLite, not the live Postgres. Step 7 is therefore a sanity check, not the production migration.
- **Proposed call:** Skip the standalone Step-7 migrate; rely on pytest's auto-migrate (Step 9) for SQLite verification, and the live web container's `migrate --noinput` startup command for Postgres. Document the choice. Override target by exporting `DJANGO_SETTINGS_MODULE=config.settings` if a one-shot host migrate is desired.
- **Reversibility:** N/A.

### #11 — Grep guard regex catches f-strings only (LOW)
- **Ambiguity:** `r'f"t_.*__b_'` matches `f"t_..."` literals but not `"t_" + tenant_id + "__b_" + bot_id` (concatenation), `"t_{}__b_{}".format(...)`, or `"t_" "..." "__b_" "..."` (string-literal joining).
- **Proposed call:** Accept the spec's f-string-only guard. The risk it targets is an inattentive contributor copy-pasting `f"t_..."` somewhere they shouldn't; concatenation-form construction would be an explicit deviation that a code review would catch. The guard is a low-friction tripwire, not an exhaustive proof.
- **Reversibility:** Trivial — extend the regex to `r'(f"|").*t_.*__b_'` if needed. No data impact.

### #12 — `null=True, blank=True` on `Document.source_filename` and `source_url` (LOW)
- **Ambiguity:** Django convention prefers empty string over null for CharField. Spec mandates BOTH `null=True, blank=True` on both fields.
- **Proposed call:** Honor the spec. Semantics: a PDF upload genuinely has *no* URL (not "empty URL"), and a URL scrape genuinely has *no* filename. Null is more honest than empty string here. Phase 5's serializers must filter to one-of-{filename, url} on input.
- **Reversibility:** Trivial migration to add a default; data is preserved.

### #13 — mypy config deferred (LOW)
- **Ambiguity:** Phase 1's implementation_report.md flags 2 missing-stub warnings (`celery`, `environ`) and recommends a Phase 2 `[tool.mypy]` config in `pyproject.toml`.
- **Proposed call:** Defer. Spec hard constraint #1 puts `pyproject.toml` on the don't-touch list, and the spec doesn't mandate mypy. Phase 2 ships with the same PARTIAL mypy state as Phase 1. Address in a future phase whose spec explicitly carves out the pyproject.toml change.
- **Reversibility:** Trivial — add the config block in any later phase.

---

## 7. Files deliberately NOT created / NOT modified

### Out of scope (per spec §"Out of scope for Phase 2"):
- DRF serializers (Phase 5)
- POST `/v1/.../documents` view (Phase 5)
- DELETE view (Phase 6)
- Qdrant collection creation/management (Phase 3)
- BGE-M3 embedder, FlagEmbedding (Phase 4)
- Chunker (Phase 4)
- Pipeline orchestrator (Phase 5)
- Postgres advisory lock acquisition (Phase 5; Phase 2 only provides the helper)
- gRPC service (Phase 7)
- `is_active=true` filter / search filters (Phase 5/7)
- Audit log table (v3)
- Atomic version swap (v2)
- Tenant/Bot CRUD endpoints (v5)
- Authentication (TBD)

### Phase 1 don't-touch list (15 files, per spec hard constraint #1):
1. `pyproject.toml`
2. `config/settings.py`
3. `config/celery.py`
4. `config/urls.py`
5. `apps/core/views.py`
6. `apps/core/logging.py`
7. `apps/core/urls.py`
8. `Dockerfile`
9. `docker-compose.yml`
10. `docker-compose.override.yml`
11. `tests/test_settings.py`
12. `tests/conftest.py`
13. `tests/test_healthz.py`
14. `scripts/verify_setup.py`
15. `.env.example`

> The 01_plan prompt parenthesises "12 files" — the actual spec list contains 15. Use this 15-file list as authoritative.

### Phase 1 apps that stay UNTOUCHED in Phase 2:
- `apps/core/` (Phase 1 views/logging/urls).
- `apps/ingestion/` (Phase 4).
- `apps/grpc_service/` (Phase 7).
- The `proto/` directory (Phase 7).
- The `scripts/` directory (only `compile_proto.sh`, `verify_setup.py` — both Phase 1 / Phase 7 owned).

### `git status` expectation between steps:
After every step, only Phase 2 files (validators.py, naming.py, models.py, admin.py, migrations, test files) should be modified. Anything else is an editor auto-save accident — `git checkout --` it before proceeding.

---

## 8. Acceptance-criteria mapping

| # | Criterion (summary) | Step that satisfies | Verification command | Expected output |
|---|---|---|---|---|
| 1 | `makemigrations` produces exactly two new migration files (committed) | Step 6 | `ls apps/{tenants,documents}/migrations/` | `__init__.py` + `0001_initial.py` in each app |
| 2 | `makemigrations --check --dry-run` exits 0 | Step 6 | `uv run python manage.py makemigrations --check --dry-run` | `No changes detected`, exit 0 |
| 3 | `ruff check .` reports zero violations | Step 10 | `uv run ruff check .` | `All checks passed!` |
| 4 | `ruff format --check .` reports zero changes needed | Step 10 | `uv run ruff format --check .` | `<N> files already formatted` |
| 5 | `pytest tests/test_models.py -v` is green | Step 9 | `uv run pytest tests/test_models.py -v` | All tests pass |
| 6 | `pytest tests/test_naming.py -v` is green incl. grep guard | Step 3 + Step 9 | `uv run pytest tests/test_naming.py -v` | All tests pass |
| 7 | Full `pytest` is green (incl. Phase 1's healthz) | Step 9 | `uv run pytest` | All tests pass |
| 8 | `docker compose up -d` brings stack up green; web container's startup applies migrations idempotently | Step 8 | `docker compose ps` + `docker compose exec postgres psql -c "\dt"` | All containers healthy/running; tables exist |
| 9 | `/healthz` regression: still 200 with both components ok | Step 11 | `curl -fsS http://localhost:8080/healthz \| python -m json.tool` | `{"status":"ok",...}` with both components ok |
| 10 | Admin login works; create Tenant → Bot (collection_name auto-populated) → Document succeeds | Step 11 | Browser walkthrough at `/admin/` | All three CRUD actions succeed |

Every criterion maps to at least one build step.

---

## 9. Tooling commands cheat-sheet

> **Local `.env` HTTP_PORT is 8080**, `POSTGRES_USER=aarav`, `POSTGRES_DB=qdrant_rag`. Adjust if your `.env` differs.
> **`manage.py check` and `makemigrations` do NOT connect to the database** — they introspect Python models only. They work without `docker compose up`. Only `migrate` needs a live DB.
> **If `docker compose` permission-denies or fails to bind a port**, see `build_prompts/phase_1_foundation/implementation_report.md` "Outstanding issues" — those are host-side, one-time-per-machine fixes (docker group, port conflicts on 5432/6379/8080). Don't re-debug Phase 1.

```bash
# === Step 1–3: helpers + helper tests ===
uv run python -c "
from apps.tenants.validators import slug_validator, validate_slug, InvalidIdentifierError, SLUG_PATTERN
validate_slug('pizzapalace')
try: validate_slug('Pizza'); raise SystemExit('FAIL')
except InvalidIdentifierError: pass
print('ok')
"
uv run python -c "
from apps.qdrant_core.naming import collection_name, advisory_lock_key
from apps.tenants.validators import InvalidIdentifierError
assert collection_name('pizzapalace','supportv1') == 't_pizzapalace__b_supportv1'
k1, k2 = advisory_lock_key('pizzapalace','supportv1','d1')
assert -(2**31) <= k1 < 2**31 and -(2**31) <= k2 < 2**31
try: collection_name('Pizza','supportv1'); raise SystemExit('FAIL')
except InvalidIdentifierError: pass
print('ok')
"
uv run pytest tests/test_naming.py -v

# === Step 4–5: models + admin (no DB writes yet) ===
uv run python manage.py check                 # zero issues
DJANGO_SETTINGS_MODULE=tests.test_settings uv run python -c "
import django; django.setup()
from apps.documents.models import Document
cols=[f.column for f in Document._meta.get_fields() if hasattr(f,'column')]
dup=[c for c in cols if cols.count(c)>1]
assert not dup, f'duplicate columns: {dup}'
print('cols ok:', cols)
"

# === Step 6: migrations (no DB connection needed) ===
uv run python manage.py makemigrations tenants documents     # scoped on purpose
uv run python manage.py makemigrations --check --dry-run     # exit 0 = no pending changes
DJANGO_SETTINGS_MODULE=tests.test_settings uv run python -c "
import os, django; django.setup()
from importlib import import_module
m_d = import_module('apps.documents.migrations.0001_initial').Migration
assert any('tenants' in str(d) for d in m_d.dependencies), 'documents missing tenants dep'
print('dep check OK')
"

# === Step 8: live Postgres migrate (via the web container's startup command) ===
docker compose -f docker-compose.yml up -d --build
docker compose ps
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT app, name FROM django_migrations WHERE app IN ('tenants','documents') ORDER BY id;"

# === Step 9: tests ===
uv run pytest -v

# === Step 10: lint ===
uv run ruff check .
uv run ruff format --check .

# === Step 11: admin smoke ===
docker compose exec -T \
  -e DJANGO_SUPERUSER_USERNAME=admin \
  -e DJANGO_SUPERUSER_EMAIL=admin@example.com \
  -e DJANGO_SUPERUSER_PASSWORD='dev-password-change-me' \
  web python manage.py createsuperuser --noinput
curl -fsS http://localhost:8080/healthz | python -m json.tool
curl -fsS http://localhost:8080/admin/login/ | grep -c "Django administration"
# Then browse to http://localhost:8080/admin/ for the Tenant→Bot→Document walkthrough.
```

**Non-obvious choices:**

- `makemigrations tenants documents` is **scoped** rather than bare (`makemigrations`). Bare picks up every installed app and could accidentally pollute the migration set if a new model file leaks into `apps/ingestion/` or another app. Scoping is defensive.
- `makemigrations --check --dry-run` is the post-generation idempotency check: re-running shows "No changes detected" only when models and migrations are in sync.
- `DJANGO_SUPERUSER_*` env vars + `--noinput` are **required** to script `createsuperuser`. Without all three the command exits non-zero. Use `-e VAR=...` flags on `docker compose exec` to pass them through to the container.
- The `docker compose -f docker-compose.yml up -d` form (no override) runs in production-mode (gunicorn, anonymous-volume venv). The default `docker compose up -d` (with override) runs `runserver`. Either works for Phase 2's verification — pick the one that matches your local config. Both run `manage.py migrate --noinput` as part of the web container's startup chain.
- `psql -U "$POSTGRES_USER"` requires `POSTGRES_USER` to be exported in the host shell. Either `source .env` first or use `docker compose exec postgres bash -c '... $POSTGRES_USER ...'` to inherit container env. The local `.env` has `POSTGRES_USER=aarav`.
- The `import_module('apps.documents.migrations.0001_initial')` call works even though the module name starts with a digit — `importlib.import_module()` accepts arbitrary strings, unlike literal `from … import …` statements which require valid identifiers.

---

## 10. Estimated effort

| Step | Wall-clock | Notes |
|---|---|---|
| 1. validators.py | 5–10 min | Verbatim from spec; only risk is a typo in `SLUG_PATTERN`. |
| 2. naming.py | 10 min | Verbatim from spec. |
| 3. test_naming.py | 20–30 min | Verbatim except for the grep→`re`+`pathlib` rewrite. Validate with a manual run. |
| 4. models.py (both apps) | **30–45 min** | **Risk #1 lurks here.** Plan time for the field-collision discovery and an escalation pause if it hits. |
| 5. admin.py (both apps) | 10 min | Mostly boilerplate. |
| 6. makemigrations | 5 min | Plus 5 min inspecting the generated files at Checkpoint F. |
| 7. SQLite migrate (optional) | 5 min | Or skip and rely on Step 9. |
| 8. Live Postgres migrate via Compose | 15–30 min | Initial `up --build` may be slow if the venv volume is stale; `down -v && up -d --build` is the cure. |
| 9. test_models.py + full pytest | 20 min | Verbatim from spec; debug per-test-class on first failure. |
| 10. ruff | 5 min | Should pass on first run if the spec is followed. |
| 11. Admin walkthrough | 15–25 min | Includes superuser provisioning + the four-step CRUD smoke. |
| 12. Report | 5–10 min | The "When you finish" output. |
| **Total** | **2.5–4 hours** | Assumes Risk #1 is resolved without an architectural escalation. If escalation is needed, add 1–4 hours for user round-trip. |

**Hot spots:** Step 4 (Risk #1) and Step 8 (Compose stack hygiene if the dev override's anonymous-volume venv is stale). Everything else is mechanical.
