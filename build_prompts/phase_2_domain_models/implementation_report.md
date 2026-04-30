# Phase 2 — Implementation Report

## Status
**OVERALL: PARTIAL**

All Phase 2 source-layer artifacts (validators, naming helpers, three Django models, two admin classes, two auto-generated migrations, two test files) were written, ruff-clean, and pytest-green on the host. Phases F (live-Postgres migrate via Compose) and H (stack-up + admin walkthrough) are blocked by the **same host-side Docker daemon permission and host-port conflicts already documented in Phase 1's implementation_report.md**. There is **one deliberate spec deviation** — the `Document.bot` FK is renamed `Document.bot_ref` because the spec's verbatim form raises Django `models.E006` (attname `bot_id` clashes with the explicit `bot_id` CharField). Plan §6 ambiguity #1 anticipated this exact escalation.

## Summary
- **Files created:** 8 (`apps/tenants/validators.py`, `apps/qdrant_core/naming.py`, `apps/tenants/models.py`, `apps/tenants/admin.py`, `apps/documents/models.py`, `apps/documents/admin.py`, `tests/test_models.py`, `tests/test_naming.py`)
- **Files replaced:** 0 (the four `apps/{tenants,documents}/{models,admin}.py` paths existed only as Phase 1 placeholders; in this build they were created fresh)
- **Files modified outside Phase 2 scope:** 0 (verified; `git status` would show only Phase 2 files plus the two auto-generated migrations)
- **Migrations generated:** 2 (`apps/tenants/migrations/0001_initial.py`, `apps/documents/migrations/0001_initial.py`)
- **Tests added:** 38 new (20 in `test_models.py`, 18 in `test_naming.py`)
- **Tests passing:** 39/39 (1 pre-existing `test_healthz` + 38 new)
- **Acceptance criteria passing:** 7/10 fully PASS · 3/10 PARTIAL/PENDING (criteria 8, 9, 10 — Compose-dependent)

## Acceptance criteria (verbatim from spec.md §"Acceptance criteria")

### Criterion 1: `uv run python manage.py makemigrations` produces exactly two new migration files: `apps/tenants/migrations/0001_initial.py` and `apps/documents/migrations/0001_initial.py`. Both committed.
- **Result:** PASS
- **Command:** `uv run python manage.py makemigrations tenants documents`
- **Output:**
  ```
  Migrations for 'tenants':
    apps/tenants/migrations/0001_initial.py
      + Create model Tenant
      + Create model Bot
  Migrations for 'documents':
    apps/documents/migrations/0001_initial.py
      + Create model Document
  ```
- **Notes:** Scoped invocation (`makemigrations tenants documents`) per plan §3 step 6. A `RuntimeWarning` about `failed to resolve host 'postgres'` was printed alongside; harmless — `makemigrations` does not need a live database connection (Phase 1 lesson; documented in plan §9 cheat-sheet).

### Criterion 2: `uv run python manage.py makemigrations --check --dry-run` exits 0 (no pending changes).
- **Result:** PASS
- **Command:** `uv run python manage.py makemigrations --check --dry-run`
- **Output:** `No changes detected` (exit 0).

### Criterion 3: `uv run ruff check .` reports zero violations.
- **Result:** PASS
- **Command:** `uv run ruff check .`
- **Output:** `All checks passed!`
- **Notes:** Required two auto-fixes during the build:
  - `I001` on `apps/documents/migrations/0001_initial.py` — Django's auto-generated import block was unsorted; `ruff check --fix` reordered it.
  - `UP012` on `apps/qdrant_core/naming.py` — spec's `.encode("utf-8")` was rewritten as `.encode()` (UTF-8 is the default; semantically identical). See *Deviations* §3.

### Criterion 4: `uv run ruff format --check .` reports zero changes needed.
- **Result:** PASS
- **Command:** `uv run ruff format --check .`
- **Output:** `40 files already formatted`

### Criterion 5: `uv run pytest tests/test_models.py -v` is green (all model tests pass).
- **Result:** PASS
- **Command:** `uv run python -m pytest tests/test_models.py -v`
- **Output:** `tests/test_models.py .................... [100%]` — 20 passed.
- **Notes:** Test cells:
  - `TestTenantModel`: 10 (`test_create_with_valid_slug_succeeds` + 8 parametrized invalid-slug rejections + `test_str`)
  - `TestBotModel`: 6 (auto-populated `collection_name`, unique constraint, cross-tenant `bot_id` reuse, unique-constraint introspection, cascade delete, `__str__`)
  - `TestDocumentModel`: 4 (UUID auto-gen, explicit doc_id, default status pending, cascade delete from bot — all use `bot_ref=b` per the FK rename deviation)

### Criterion 6: `uv run pytest tests/test_naming.py -v` is green, including the codebase-grep guard test.
- **Result:** PASS
- **Command:** `uv run python -m pytest tests/test_naming.py -v`
- **Output:** `tests/test_naming.py .................. [100%]` — 18 passed.
- **Notes:** The grep guard (`TestNoOtherCollectionNameConstructors`) was rewritten in pure Python (`pathlib.rglob` + `re.compile`) per plan rev 2 §3 step 3 to remove the GNU-grep dependency. The regex `r'f"t_.*__b_'` matches the same f-string literal the spec's `subprocess.run(["grep", "-rEn", "f\"t_.*__b_", ...])` would have. The guard finds zero unauthorized constructors in `apps/` and `config/`.

### Criterion 7: `uv run pytest` (full suite) is green — Phase 1's healthz test still passes alongside the new tests.
- **Result:** PASS
- **Command:** `uv run python -m pytest -v`
- **Output:**
  ```
  tests/test_healthz.py .                                                  [  2%]
  tests/test_models.py ....................                                [ 53%]
  tests/test_naming.py ..................                                  [100%]
  39 passed, 1 warning in 5.19s
  ```
- **Notes:** The single warning is a pre-existing `UserWarning: Api key is used with an insecure connection` from Phase 1's `apps/core/views.py:23` (Qdrant client warning when reaching for the cached singleton during the healthz test). Not a Phase 2 regression.

### Criterion 8: `docker compose -f docker-compose.yml up -d` brings the stack up green; `python manage.py migrate` (run by the web container's startup command) applies the new migrations idempotently.
- **Result:** PENDING — blocked by host-side Docker daemon permission (same as Phase 1 outstanding issue #1).
- **Command attempted:** `docker compose ps`
- **Output:** `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`
- **Indirect verification:**
  - `docker compose -f docker-compose.yml config --services` lists all 6 services (postgres, qdrant, redis, web, grpc, worker) — Compose YAML is valid.
  - The two new migrations are inspectable on disk and were verified by Python introspection: `apps.documents.migrations.0001_initial.Migration.dependencies = [('tenants', '0001_initial')]`.
  - The web container's `command:` in `docker-compose.yml` (Phase 1, locked) chains `python manage.py migrate --noinput` before launching gunicorn, so the migrations will apply on the next stack-up.
- **User action required:** see *Outstanding issues* below.

### Criterion 9: `curl -fsS http://localhost:8080/healthz | python -m json.tool` still returns 200 with both components ok (regression check on Phase 1).
- **Result:** PENDING — blocked by Criterion 8.
- **Indirect verification:** Phase 1's `tests/test_healthz.py` still passes against the SQLite test overlay (1/1). No Phase 2 file modified `apps/core/views.py`, `apps/core/urls.py`, `apps/core/logging.py`, or `config/settings.py`. The healthz handler is unchanged.

### Criterion 10: Django admin login works; create Tenant → create Bot (collection_name auto-populates read-only) → create Document.
- **Result:** PENDING — blocked by Criterion 8.
- **Indirect verification:**
  - `manage.py check` returns 0 issues with both admin classes registered (`@admin.register(Tenant)`, `@admin.register(Bot)`, `@admin.register(Document)`).
  - `BotAdmin.readonly_fields = ("collection_name", "created_at")` per spec.
  - `Bot.save()` is verified by `tests/test_models.py::TestBotModel::test_collection_name_auto_populated_on_save` — `collection_name == "t_pizzapalace__b_supportv1"` after `Bot.objects.create(tenant=t, bot_id="supportv1", name="Support")`.
  - `DocumentAdmin.readonly_fields = ("doc_id", "uploaded_at", "last_refreshed_at", "chunk_count", "item_count")` per spec.

## Pitfall avoidance (verbatim from spec.md §"Common pitfalls")

### Pitfall 1: Circular imports between `apps.tenants.models` and `apps.qdrant_core.naming`.
- **Status:** Avoided.
- **How confirmed:** `apps/tenants/validators.py` has zero Django model imports. `apps/qdrant_core/naming.py` imports only `validate_slug` from `apps.tenants.validators`. `Bot.save()` does the function-local import: `from apps.qdrant_core.naming import collection_name as _collection_name`. The import-smoke checkpoints at Steps 1 and 2 succeeded, and `manage.py check` confirms the app registry loads without cycle errors.

### Pitfall 2: Bot.save() not auto-populating collection_name.
- **Status:** Avoided.
- **How confirmed:** `apps/tenants/models.py:42-46` computes `collection_name` BEFORE calling `super().save(*args, **kwargs)`. The `*args, **kwargs` forwarding preserves Django's `force_insert`/`force_update`/`using`/`update_fields` semantics. `tests/test_models.py::TestBotModel::test_collection_name_auto_populated_on_save` passes.

### Pitfall 3: `@pytest.mark.django_db` missing on model tests.
- **Status:** Avoided.
- **How confirmed:** All three test classes in `tests/test_models.py` have the class-level `@pytest.mark.django_db` decorator. Pytest output shows 20/20 model tests passing — would emit `RuntimeError: Database access not allowed` if the marker were missing.

### Pitfall 4: Migration files committed before the schema is finalized.
- **Status:** Avoided.
- **How confirmed:** Models were finalized (manage.py check + field-collision pre-check both green) before `makemigrations` ran. Both 0001_initial.py files contain a single `CreateModel` operation per app — no `AlterField` follow-ups, confirming a single clean iteration.

### Pitfall 5: collection_name field manually set in admin or shell.
- **Status:** Avoided.
- **How confirmed:** `BotAdmin.readonly_fields = ("collection_name", "created_at")` makes the field non-editable in admin. `Bot.save()` always overwrites whatever value was set, by design (`tests/test_models.py::TestBotModel::test_collection_name_auto_populated_on_save` would fail if the assignment were skipped).

### Pitfall 6: Document denormalization drifts from Bot.
- **Status:** N/A in Phase 2.
- **How confirmed:** Spec defers enforcement of `Document.tenant_id == bot.tenant_id` and `Document.bot_id == bot.bot_id` to Phase 5's pipeline (per spec §"`apps/documents/models.py`" notes). Phase 2 doesn't add a `clean()`. The model accepts blank denormalization fields if the caller is sloppy. Confirmed by the test pattern `Document.objects.create(bot_ref=b, tenant_id=b.tenant_id, bot_id=b.bot_id, ...)`.

### Pitfall 7: UniqueConstraint vs unique_together.
- **Status:** Avoided.
- **How confirmed:** `apps/tenants/models.py:33-37` uses `models.UniqueConstraint(fields=["tenant", "bot_id"], name="unique_bot_per_tenant")` — modern Django syntax. The auto-generated migration encodes it as `models.UniqueConstraint(fields=("tenant", "bot_id"), name="unique_bot_per_tenant")`.

### Pitfall 8: Forgetting `null=True, blank=True` on optional URL/filename fields.
- **Status:** Avoided.
- **How confirmed:** `apps/documents/models.py:32-33` retains `null=True, blank=True` on both `source_filename` and `source_url`. Migration 0001_initial encodes both with `blank=True, null=True`.

### Pitfall 9: `auto_now_add` vs `auto_now`.
- **Status:** Avoided.
- **How confirmed:** `apps/documents/models.py:40-41` — `uploaded_at = DateTimeField(auto_now_add=True)` (set once at creation), `last_refreshed_at = DateTimeField(auto_now=True)` (updates on every save). Migration encodes both correctly.

### Pitfall 10: `Bot._meta.get_field(...)` AppRegistryNotReady.
- **Status:** Avoided.
- **How confirmed:** `tests/test_models.py::TestBotModel::test_collection_name_unique_constraint` introspects via `Bot._meta.get_field("collection_name")` inside a test body (after pytest-django's auto django.setup). The test passes — no `AppRegistryNotReady` error.

## Out-of-scope confirmation

Confirmed not implemented (per spec §"Out of scope for Phase 2"):

- DRF serializers for documents — Phase 5: confirmed not implemented.
- POST `/v1/.../documents` endpoint — Phase 5: confirmed not implemented.
- DELETE endpoint — Phase 6: confirmed not implemented.
- Qdrant collection creation/management — Phase 3: confirmed not implemented.
- BGE-M3 embedder — Phase 4: confirmed not implemented.
- Chunker — Phase 4: confirmed not implemented.
- Pipeline orchestrator — Phase 5: confirmed not implemented.
- Postgres advisory lock acquisition — Phase 5: confirmed not implemented (only the `advisory_lock_key` helper is provided).
- gRPC service implementation — Phase 7: confirmed not implemented.
- Search filters / `is_active` flag enforcement — Phase 5/7: confirmed not implemented.
- Audit log table — v3: confirmed not implemented.
- Atomic version swap — v2: confirmed not implemented.
- Tenant/Bot CRUD endpoints — v5: confirmed not implemented.
- Authentication — TBD: confirmed not implemented (DRF still `AllowAny` per Phase 1).

## Phase 1 regression check

- ✓ All 15 Phase 1 don't-touch files have mtimes pre-dating this Phase 2 session (verified with `stat -c '%Y %n'`).
- ✓ `tests/test_healthz.py` still passes (1/1) under the SQLite test overlay.
- ✓ `apps/core/{views,logging,urls}.py`, `config/{settings,celery,urls,wsgi,asgi}.py`, `Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`, `tests/{test_settings,conftest,test_healthz}.py`, `scripts/verify_setup.py`, `.env.example`, `pyproject.toml` — none modified.
- `git diff --name-only` (if a git repo were initialized — see *Outstanding issues* below) would list only Phase 2 files: validators.py, naming.py, two models.py, two admin.py, two 0001_initial.py migrations, test_models.py, test_naming.py.

## Deviations from plan / spec

### Deviation 1 (CRITICAL — anticipated): rename `Document.bot` → `Document.bot_ref`
- **What:** Renamed the FK from `bot` to `bot_ref` on `Document`. `related_name="documents"` is preserved, so `bot_instance.documents.all()` still works from Bot's side. Test code uses `bot_ref=b` instead of `bot=b` in `Document.objects.create(...)` calls.
- **Why:** Spec body declares both `bot = ForeignKey(Bot, ...)` (default attname `bot_id`) AND `bot_id = CharField(...)`. Django's system check raises `models.E006: The field 'bot_id' clashes with the field 'bot' from model 'documents.document'`. The plan's §6 ambiguity #1 had proposed `db_column='bot_pk'` as one resolution candidate — that proposal was inaccurate, because `db_column` only changes the SQL column, not the Python `attname` (which is hard-coded to `<field_name>_id` for ForeignKey). The only fixes are (A) rename the FK, or (B) rename the CharField. (A) has the smaller surface (one field name change vs two), so I chose `bot_ref`.
- **Impact:** Phase 5+ code that traverses the FK now uses `doc.bot_ref` (not `doc.bot`). Reverse traversal is unchanged: `bot.documents.all()`. The denormalized CharFields (`Document.tenant_id`, `Document.bot_id`) are preserved and still align with the spec's terminology and the URL-path identifiers. Migration column for the FK is `bot_ref_id` (Django default).
- **Reversibility:** Possible but expensive once Phase 5+ code accumulates. The user may override by renaming back: change `bot_ref` → `bot` in `apps/documents/models.py` AND rename one of the conflicting fields (e.g., `bot_id` → `bot_slug`), then `makemigrations` to capture the schema change.
- **Recommendation:** **Confirm with the user before Phase 3 begins.** This is a locked-context change per the scoping-partner rule; the auto-mode action was made because the build was non-functional otherwise, but the user should ratify (or override) before downstream phases hard-code `doc.bot_ref`.

### Deviation 2: omit unused `from django.core.exceptions import ValidationError` in `apps/tenants/validators.py`
- **What:** Spec's verbatim body imports `ValidationError` but never uses it.
- **Why:** Ruff's `F401` (unused import) would fire under the project's lint config (`pyproject.toml` `[tool.ruff.lint].select = ["E", "F", "I", ...]`), failing acceptance criterion 3.
- **Impact:** None — `ValidationError` is not referenced anywhere in `validators.py`. Future code that needs it will import it explicitly.
- **Reversibility:** Trivial — re-add the line and silence the linter via `# noqa: F401` if desired.

### Deviation 3: rewrite `.encode("utf-8")` as `.encode()` in `apps/qdrant_core/naming.py`
- **What:** Spec uses `.encode("utf-8")`; Phase 2 uses `.encode()` (UTF-8 is the default).
- **Why:** Ruff's `UP012` flagged the explicit argument as unnecessary. Acceptance criterion 3 requires zero ruff violations.
- **Impact:** Semantically identical. Both produce the same byte string for the same input.
- **Reversibility:** Trivial.

### Deviation 4: replace `subprocess.run(["grep", ...])` with `pathlib.rglob` + `re.compile` in `tests/test_naming.py`
- **What:** Spec's grep test shells out to GNU grep. Phase 2 implements the same scan in pure Python.
- **Why:** Plan rev 2 §3 step 3 (resolves plan_review.md L7 finding). Removes the binary dependency for portability across host / Docker / macOS / Windows.
- **Impact:** Same regex (`r'f"t_.*__b_'`), same search roots (`apps/`, `config/`), same exclusion (`apps/qdrant_core/naming.py`). Zero behavioral difference for the cases the spec test asserts.
- **Reversibility:** Trivial — restore `subprocess.run(["grep", "-rEn", ...])` if the project ever standardizes on GNU grep being available.

## Spec defects discovered

1. **`Document.bot` and `Document.bot_id` cause `models.E006`.** See Deviation 1. The spec's verbatim body cannot be applied as-is. The plan §6 ambiguity #1 anticipated this with proposed resolutions; the resolution proposal `db_column='bot_pk'` was itself inaccurate (Django attname is field-name-derived and ignores `db_column`). The actual fix requires renaming a field; this implementation chose to rename the FK.

2. **`apps/tenants/validators.py` imports `ValidationError` but doesn't use it.** Causes `ruff F401`. See Deviation 2.

3. **`apps/qdrant_core/naming.py` `.encode("utf-8")` triggers `ruff UP012`.** See Deviation 3.

4. **`tests/test_naming.py` `subprocess.run(["grep", ...])` pins to a GNU-grep host.** See Deviation 4.

5. **Spec §"Deliverables" prose says "9 new / replaced files"; the tree contains 10.** Cosmetic. Plan §0 already reconciles.

## Outstanding issues

These mirror Phase 1's outstanding issues. None is a Phase 2 code defect.

1. **Docker daemon socket permission denied for user `bol7`.**
   - **Symptom:** `docker compose ps`, `docker compose up -d`, `docker compose exec` all return `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`.
   - **Fix (per Phase 1 implementation_report.md):**
     ```bash
     sudo usermod -aG docker bol7
     newgrp docker          # OR log out and back in
     ```

2. **Host port conflicts on 5432, 6379, 8080 (likely unchanged from Phase 1).**
   - **Fix (per Phase 1 implementation_report.md):**
     ```bash
     sudo systemctl stop postgresql redis-server
     pkill -f 'DynamicADK.*manage.py runserver' || true
     ```

3. **Repo is not a git repo (`is a git repository: false` in environment header).**
   - **Symptom:** `git status` / `git diff` cannot be used to verify Phase-1-don't-touch invariant; we used `stat -c '%Y'` mtimes instead.
   - **Fix (optional):** `git init && git add -A && git commit -m "phase 2 baseline"` once the user is happy with the Phase 2 deliverable. Not blocking.

4. **mypy state stays at Phase 1's PARTIAL.**
   - **Symptom:** `uv run mypy apps/ config/` would emit 2 missing-stub warnings for `celery` / `environ`.
   - **Reason for deferral:** Spec hard constraint #1 puts `pyproject.toml` on the don't-touch list, and Phase 2's spec doesn't mandate mypy. See plan §6 ambiguity #13.
   - **Fix:** A future phase whose spec carves out the `pyproject.toml` change can add `[tool.mypy] ignore_missing_imports = true` plus per-module overrides for `celery.*` and `environ.*`.

## Files created or replaced

```
apps/documents/admin.py
apps/documents/migrations/0001_initial.py
apps/documents/models.py
apps/qdrant_core/naming.py
apps/tenants/admin.py
apps/tenants/migrations/0001_initial.py
apps/tenants/models.py
apps/tenants/validators.py
tests/test_models.py
tests/test_naming.py
```

10 paths total. 8 hand-written + 2 Django-auto-generated migrations.

## Generated migrations

### `apps/tenants/migrations/0001_initial.py`
- `dependencies = []`
- `CreateModel` operations: `Tenant` (tenant_id PK CharField + slug RegexValidator, name CharField, created_at DateTimeField), `Bot` (id BigAutoField PK, bot_id CharField + slug RegexValidator, name, collection_name CharField unique, created_at, tenant ForeignKey CASCADE related_name="bots", `UniqueConstraint(["tenant", "bot_id"], name="unique_bot_per_tenant")`, `Index(["tenant", "bot_id"])`).

### `apps/documents/migrations/0001_initial.py`
- `dependencies = [("tenants", "0001_initial")]`
- `CreateModel` operations: `Document` (doc_id UUIDField PK default uuid4, tenant_id CharField + slug, bot_id CharField + slug, source_type, source_filename CharField null/blank, source_url TextField null/blank, content_hash, chunk_count default 0, item_count default 0, status with 4 choices default pending, error_message TextField null/blank, uploaded_at DateTimeField auto_now_add, last_refreshed_at DateTimeField auto_now, **bot_ref ForeignKey CASCADE related_name="documents"** (per Deviation 1), indexes on `[tenant_id, bot_id]` and `[status]`).

## Commands to verify the build (one block, copy-pasteable)

After resolving the two Phase 1 outstanding issues (docker group + port conflicts):

```bash
cd /home/bol7/Documents/BOL7/Qdrant

# One-time host fixes (already required for Phase 1)
sudo usermod -aG docker bol7
newgrp docker
sudo systemctl stop postgresql redis-server
pkill -f 'DynamicADK.*manage.py runserver' || true

# Code-level (no docker required)
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python -m pytest -v

# Stack-up (production mode)
docker compose -f docker-compose.yml down -v 2>/dev/null
docker compose -f docker-compose.yml up -d --build
sleep 90
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml exec web python manage.py migrate --check  # 0 pending
docker compose -f docker-compose.yml exec postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "SELECT app, name FROM django_migrations WHERE app IN ('tenants','documents') ORDER BY id;"

# Healthz regression (criterion 9)
curl -fsS http://localhost:8080/healthz | python -m json.tool

# Admin login + walkthrough (criterion 10)
curl -fsS http://localhost:8080/admin/login/ | grep -c "Django administration"
docker compose -f docker-compose.yml exec -T \
    -e DJANGO_SUPERUSER_USERNAME=admin \
    -e DJANGO_SUPERUSER_EMAIL=admin@example.com \
    -e DJANGO_SUPERUSER_PASSWORD='dev-password-change-me' \
    web python manage.py createsuperuser --noinput
# Then browse to http://localhost:8080/admin/ and create Tenant → Bot → Document.

# Cleanup
docker compose -f docker-compose.yml down
```

## Verdict

Phase 2 source layer is **complete and self-consistent**: 10 of 10 spec deliverables created (with one anticipated FK rename for `Document.bot_ref`), all 39 tests green, ruff clean, model invariants confirmed via parametrized test suite. The build is **not shippable end-to-end until the user runs the four sudo commands at the top of the verify block** — those are unchanged from Phase 1's outstanding issues and require host-side resolution. **Before kicking off Phase 3, confirm the `Document.bot` → `Document.bot_ref` rename** (Deviation 1) — that's the one architectural decision the implementer made that the user may want to override.

**User's next step:**
1. Run the host-side fixes to unblock Compose.
2. Execute the verify block above and confirm criteria 8/9/10 turn green.
3. Decide on the FK-rename: keep `bot_ref` (recommended; smallest impact) or override to a different name. If overridden, regenerate migrations and update test_models.py.
4. If 1–3 are clean, **Phase 3 (Qdrant Layer) is unblocked.**
