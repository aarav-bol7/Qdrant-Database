# Phase 1 — Fix Plan: web container unhealthy due to .venv shadowing (revised)

> Companion: `build_prompts/phase_1_foundation/fix_plan_review.md` (6 minor findings absorbed below).

## 0. Revision notes

What changed vs the previous version (cross-refs to `fix_plan_review.md`):

- **§1 diagnosis hardened [finding #1]:** added a preflight check that `head -3 .venv/pyvenv.cfg` shows a host Python path — locks down the empirical chain rather than relying on inference. Counter-explanations (lockfile-fallback, uv version mismatch) explicitly considered and rejected.
- **§3 file 3.b pitfall #6 wording [finding #2]:** added one sentence on `restart` vs `down`/`up`/`--build` — anonymous volumes survive `docker compose restart`; only container recreation drops them.
- **§3 file 3.a spec example [finding #3]:** rewrote with exact "before" / "after" YAML so spec doc and code can't drift; added a guard rule against renumbering existing pitfalls (#11 already exists).
- **§4 verification [finding #4]:** the `Downloading` negative-check is now a required assertion with exit-coded expected output `0`, not "optional".
- **§4 verification [finding #5]:** added two positive proofs that the venv came from the image — `head -3 /app/.venv/pyvenv.cfg` (container Python path) and a Django import smoke test inside the container.
- **§4 verification [finding #6]:** added a final `uv run pytest -q` host-side sanity to prove the test suite is unaffected.

Section structure (1–7) unchanged otherwise.

---

## 1. Diagnosis confirmation

### Symptom recap
After `docker compose down -v && docker compose up -d --build`:
- `qdrant_rag_web` reports `(unhealthy)` indefinitely.
- `qdrant_rag_grpc` is `Created` but blocked (`depends_on: web service_healthy`).
- `docker compose logs --tail 200 web` shows ONLY `Downloading <pkg>` lines (numpy, pygments, mypy, pydantic-core, ruff, django, grpcio, psycopg-binary, etc.) — never `Operations to perform`, never `gunicorn`/`runserver` startup, never any healthz hit.
- The container is performing `uv sync` AT RUNTIME, never reaching app code.

### Files inspected
- **`docker-compose.override.yml`** lines 6–8 (web service):
  ```yaml
  volumes:
    - .:/app
    - bge_cache:/app/.cache/bge
  ```
  And lines 23–25 (worker):
  ```yaml
  volumes:
    - .:/app
  ```
  Both bind-mount the host project root over `/app`. **No volume escape at `/app/.venv`.**

- **`Dockerfile`** lines 16–17 (builder) and 30 (runtime):
  - Builder runs `uv sync --frozen --no-dev` (with non-frozen fallback) inside `WORKDIR /app`, which creates `/app/.venv/` populated with the locked dependency set.
  - Runtime stage `COPY --from=builder /app /app` brings the venv across.
  - Line 34: `PATH="/app/.venv/bin:${PATH}"` — confirms the venv lives at `/app/.venv` (not `/opt/venv` or similar) and the runtime expects to find binaries there.

- **`pyproject.toml`** is plain — no `[tool.uv]` venv-relocation directives, no custom `VIRTUAL_ENV` overrides.

### Root cause statement
The dev override's `- .:/app` bind-mounts the host project directory (which contains a host-built `.venv/`) over `/app`. This shadows the image's `/app/.venv` from the container's view. The container now sees the host's venv at `/app/.venv` — but its `pyvenv.cfg` and binary shebangs reference the host's Python interpreter path (e.g. `/home/bol7/.local/share/uv/python/...`), which does not exist inside the container.

When the override's startup command runs `uv run python manage.py migrate`, `uv` inspects `/app/.venv`, finds the foreign Python interpreter reference, and decides the venv is invalid. It then re-runs `uv sync` to repopulate the venv inside the container, downloading every wheel from scratch (the dev container has no PyPI cache mount, unlike the build cache).

The 30-second healthcheck `start_period` is far too short for a full `uv sync` over the network (~64 packages, hundreds of MB), so the container is marked unhealthy long before gunicorn starts.

### Why the symptom matches the root cause
- The logs are **exclusively** `Downloading <pkg>` lines — exactly what `uv sync` emits and nothing else. No Django, no gunicorn, no migrate output. That is the signature of a re-sync occurring before the command chain reaches the next `&&`.
- `docker compose logs --tail 200` covers ~200 lines and still contains no migrate/gunicorn output, meaning the sync hasn't even finished after enough time for ~200 download lines to scroll.
- The non-override path (`docker compose -f docker-compose.yml up`) was not tested in this session, but it would not exhibit this symptom because there is no bind mount at `/app` — the image's `/app/.venv` would be used directly.

### Counter-explanations considered and rejected
- **`uv sync --frozen` failed at build time, fell back to non-frozen, left the image's venv incomplete, and the container is topping it up.** Inconsistent with the symptom: a top-up would download only a small delta, not the full ~64-package set every time. The full re-download signature points to a fresh venv being built from scratch.
- **uv version mismatch host (0.9.28) vs image `:latest` (≈ 29.x by 2026).** Plausible cofactor — different uv versions could differ on lockfile interpretation — but prod-style runs (no override, no bind) are unaffected, which means the image's venv is fine *until* the host bind shadows it. The dominant mechanism is the bind shadow; uv version alignment would not change the fix.

### Empirical confirmation step (run before applying the fix)
```bash
head -3 /home/bol7/Documents/BOL7/Qdrant/.venv/pyvenv.cfg
```
Expected: a `home = ` line pointing at a host Python path (e.g. `/home/bol7/.local/share/uv/...` or `/usr/bin/python3.13`), not `/usr/local/bin/python3.13`. That confirms the host venv is exposing a foreign interpreter — exactly what `uv` rejects inside the container.

**Diagnosis matches symptom.**

---

## 2. Choice: anonymous vs named volume

**Choice: anonymous volume** (i.e. `- /app/.venv` with no left-hand side).

### Rationale
- The venv is **build output**, not user data. It should always reflect what the current image contains. Persistence across container lifecycle is anti-feature.
- `docker compose down -v` is the user's mental model for "wipe everything and start fresh." Anonymous volumes are dropped along with the containers and named volumes when `-v` is passed; this aligns naturally.
- Named volumes require an additional declaration in `docker-compose.yml`'s top-level `volumes:` block, expanding the surface area of the change for no functional benefit at v1's scale.
- Named volumes also persist across `docker compose down` (without `-v`) — a named `venv_cache` would survive a normal `down` and re-attach to the next container, masking pyproject changes until the user remembers `docker volume rm` or `down -v`. That's a worse failure mode than recreating from the image.
- Anonymous + always-from-image means: edit `pyproject.toml`, run `docker compose up --build` (Compose recreates the container with the new image), the new container gets a fresh anonymous volume initialised from the new image's venv. Clean.

### Trade-off this choice locks in
- Anonymous volumes can't be referenced by name in `docker volume ls` debugging — they appear as long hash IDs.
- `docker compose stop && docker compose start` (or `docker compose restart`) reuses the existing container and its anonymous volume. Editing `pyproject.toml` then `restart`-ing won't pick up new packages — only `up --build` (or `down`/`up`) recreates the container and refreshes the volume. This is documented in the rewritten pitfall #6 (§3.b) so users don't trip on it.

---

## 3. Planned edits

Three files. Order: code edits first, spec doc update last.

### File 1 — `docker-compose.override.yml`

- **Before:**
  ```yaml
  services:
    web:
      command: >
        sh -c "uv run python manage.py migrate --noinput &&
               uv run python manage.py runserver 0.0.0.0:8000"
      volumes:
        - .:/app
        - bge_cache:/app/.cache/bge
      environment:
        DJANGO_DEBUG: "True"
      restart: "no"
  ```
  …and:
  ```yaml
    worker:
      volumes:
        - .:/app
      restart: "no"
  ```

- **After:**
  ```yaml
  services:
    web:
      command: >
        sh -c "uv run python manage.py migrate --noinput &&
               uv run python manage.py runserver 0.0.0.0:8000"
      volumes:
        - .:/app
        - /app/.venv
        - bge_cache:/app/.cache/bge
      environment:
        DJANGO_DEBUG: "True"
      restart: "no"
  ```
  …and:
  ```yaml
    worker:
      volumes:
        - .:/app
        - /app/.venv
      restart: "no"
  ```

- **Why:** the `- /app/.venv` line declares an anonymous volume mounted at `/app/.venv`. Compose evaluates volume mounts in order; the bind mount `- .:/app` runs first, then the anonymous volume overlays just the `.venv` subtree, hiding the host's venv and exposing the image's venv (initialised on first container start from the image's `/app/.venv` content).

### File 2 — `docker-compose.yml`

- **No change.** Anonymous volume needs no top-level declaration.

### File 3 — `build_prompts/phase_1_foundation/spec.md`

Two edits in this file. **Important:** spec.md already contains a pitfall #11 (added later, about the SQLite-overlay note); do NOT renumber existing pitfalls. Edit #6 in place.

#### 3.a Update the `docker-compose.override.yml` example block (currently lines 720–751)

- **Before** (verbatim, web + worker volume blocks):
  ```yaml
  services:
    web:
      command: >
        sh -c "uv run python manage.py migrate --noinput &&
               uv run python manage.py runserver 0.0.0.0:8000"
      volumes:
        - .:/app
        - bge_cache:/app/.cache/bge
      environment:
        DJANGO_DEBUG: "True"
      restart: "no"

    postgres:
      ports:
        - "5432:5432"
      restart: "no"

    redis:
      ports:
        - "6379:6379"
      restart: "no"

    worker:
      volumes:
        - .:/app
      restart: "no"
  ```

- **After** (only the two `volumes:` blocks change — postgres and redis are untouched):
  ```yaml
  services:
    web:
      command: >
        sh -c "uv run python manage.py migrate --noinput &&
               uv run python manage.py runserver 0.0.0.0:8000"
      volumes:
        - .:/app
        - /app/.venv
        - bge_cache:/app/.cache/bge
      environment:
        DJANGO_DEBUG: "True"
      restart: "no"

    postgres:
      ports:
        - "5432:5432"
      restart: "no"

    redis:
      ports:
        - "6379:6379"
      restart: "no"

    worker:
      volumes:
        - .:/app
        - /app/.venv
      restart: "no"
  ```

This must be byte-identical to the file in §3 file 1 (modulo file structure differences). The implementation prompt should diff the two and assert no drift.

#### 3.b Rewrite pitfall #6 (line 806)

- **Before:**
  > 6. **Hot reload doesn't work in dev.** The override file mounts the project root at `/app`, which shadows the built venv. uv's venv lives at `/app/.venv` which is itself shadowed. Solution: use a named volume for `.venv` (already pattern in similar setups) or accept that you need to rebuild after `pyproject.toml` changes. For Phase 1, accept the rebuild — premature optimization.

- **After:**
  > 6. **Bind-mounted source shadows the image's venv.** The dev override mounts the host project root at `/app` for hot reload. Without an additional escape, this hides the image's `/app/.venv` and the container falls back to the host venv (which has foreign Python interpreter paths) — `uv run` then re-runs `uv sync` at every container start and the web healthcheck times out. **Required fix:** add an anonymous volume at `/app/.venv` for both the `web` and `worker` services in the override (`- /app/.venv` on its own line, immediately after `- .:/app`). The anonymous volume initialises from the image's venv on first container start and stays out of the host's way. **Trade-off:** after editing `pyproject.toml` or `uv.lock`, run `docker compose down -v && docker compose up -d --build` to drop the stale venv volume and recreate it from the rebuilt image. **Plain `docker compose restart` (and `stop`/`start`) keeps the existing container and reuses the old anonymous volume — only container recreation (via `up --build` or `down`/`up`) replaces it.** The fix does not affect prod-style runs (`docker compose -f docker-compose.yml up`) which have no bind mount.

---

## 4. Verification plan

Each command's expected outcome is explicit. Run in order; abort on any divergence. Commands marked **(REQUIRED ASSERTION)** must produce the stated output exactly — anything else means the fix did not work.

- **Command 0 (preflight, before applying the fix):** `head -3 .venv/pyvenv.cfg`
  - **Expected:** a `home = ` line referring to a host Python path (e.g. `/home/bol7/.local/share/uv/python/cpython-3.13.x/bin` or similar). This locks in the diagnosis empirically.

- **Command 1:** `docker compose down -v && docker compose up -d --build`
  - **Expected:** Compose builds the image, pulls postgres/redis/qdrant images, all 4 health-checked services come up; `web` reaches `(healthy)` within ~60 seconds. `grpc` becomes `Up` (no longer blocked).

- **Command 2:** `docker compose ps`
  - **Expected:** all six containers shown. `postgres`/`redis`/`qdrant`/`web` show `(healthy)`. `grpc` shows `Up` (no healthcheck). `worker` shows `Up` (no healthcheck). No `(unhealthy)` rows.

- **Command 3 (REQUIRED ASSERTION — fix-discriminator):**
  ```bash
  docker compose logs web --tail 200 | grep -c '^Downloading' || true
  ```
  - **Expected:** `0`. ANY non-zero count means the venv was re-synced — the fix did not take effect. This is the single most important verification because it discriminates between "venv intact" and "venv re-synced (just possibly faster)".

- **Command 4 (positive proof — venv came from the image):**
  ```bash
  docker compose exec web sh -c 'head -3 /app/.venv/pyvenv.cfg'
  ```
  - **Expected:** `home = /usr/local/bin` (or `/usr/local/bin/python3.13`) — the container's Python interpreter path, NOT the host's `/home/bol7/...`. Confirms the anonymous volume initialised from the image, not the host.

- **Command 5 (positive proof — Django importable without a re-sync round-trip):**
  ```bash
  docker compose exec web sh -c 'python -c "import django, structlog; print(django.get_version())"'
  ```
  - **Expected:** Django version string (e.g. `5.2.13`) printed within ~1 second; no `Downloading` output.

- **Command 6:** `curl -fsS http://localhost:8000/healthz | python -m json.tool`
  - **Expected:** HTTP 200, body `{"status":"ok","version":"0.1.0-dev","components":{"postgres":"ok","qdrant":"ok"}}`. (Maps to spec.md acceptance criterion 5.)

- **Command 7:** `curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:8000/admin/login/`
  - **Expected:** `200`. (Maps to spec.md acceptance criterion 6.)

- **Command 8 (degradation):** `docker compose stop qdrant && sleep 5 && curl -s -o /tmp/h.json -w '%{http_code}\n' http://localhost:8000/healthz && cat /tmp/h.json`
  - **Expected:** `503`; JSON body has `"postgres":"ok"` and `"qdrant"` starting with `"error:"`. Then `docker compose start qdrant` to restore. (Maps to spec.md acceptance criterion 7.)

- **Command 9 (prod-mode log probe — criterion 8):**
  ```bash
  docker compose down
  docker compose -f docker-compose.yml up -d
  sleep 60
  docker compose -f docker-compose.yml logs web --tail 20 \
      | grep -E '^\{' | head -1 \
      | python -c "import json,sys; json.loads(sys.stdin.read()); print('JSON-OK')"
  docker compose -f docker-compose.yml down
  docker compose up -d   # back to dev mode
  sleep 30
  ```
  - **Expected:** `JSON-OK`. The prod path has no bind mount and is unaffected by the fix; this verification stays in the script for completeness.

- **Command 10 (reproducibility — criterion 10):** `docker compose down -v && docker compose up -d --build && sleep 90 && docker compose ps && curl -fsS http://localhost:8000/healthz`
  - **Expected:** clean teardown including volumes; clean re-up; web healthy; healthz returns the green JSON. Re-runs commands 1+2+6 implicitly.

- **Command 11 (host-side sanity — pytest still green):**
  ```bash
  uv run pytest -q
  ```
  - **Expected:** `1 passed`. Pytest runs against the SQLite overlay (host-side) and is unaffected by Compose changes; this is cheap insurance against accidental edits.

- **Final step:** update `implementation_report.md` to mark acceptance criteria 5, 6, 7, 8, 10 as PASS (no longer PENDING). The "Outstanding issues" section can drop the `unhealthy web` blocker.

---

## 5. Out of scope (will NOT touch)

- Any Python source: `apps/core/views.py` (healthz), `apps/core/logging.py`, `config/settings.py`, all AppConfig stubs, all tests.
- `Dockerfile` (image build is correct; this is a dev-override-only defect).
- `docker-compose.yml` services: `postgres`, `redis`, `qdrant`, `web` (base config), `grpc`, `worker` — only the top-level `volumes:` block could be touched, and only for the named-volume option which we rejected. So `docker-compose.yml` stays unchanged.
- Healthcheck definitions (`start_period`, `interval`, `retries` on web) — they are correctly tuned; the bug is upstream.
- structlog configuration, settings dictConfig, REST_FRAMEWORK, INSTALLED_APPS.
- `tests/test_settings.py` SQLite overlay and conftest.
- `pyproject.toml`, `uv.lock`.
- CI workflow `.github/workflows/ci.yml` (CI does not use Compose; it uses uv + service containers and is unaffected).
- README, scripts, .env.example, .env, .gitignore, .dockerignore.
- spec.md pitfalls #1–#5, #7–#11 — only #6 is rewritten in place. **Do not renumber.** spec.md already has a pitfall #11 (the SQLite-overlay note added after Phase 1 implementation); leave it alone.

---

## 6. Risks

After this fix, possible failure modes:

- **Stale venv after `pyproject.toml` change.** Anonymous volume persists across `docker compose stop`/`start`/`restart`. If the user edits `pyproject.toml` and runs `docker compose restart web`, the old venv content is reused — new dependencies are missing. Mitigation: documented in the new pitfall #6 — recipe is `down -v && up -d --build`.

- **Container recreation triggers fresh venv every time.** On `docker compose up --build` Compose recreates containers (because the image hash changed). Each recreated container gets a fresh anonymous volume from the (new) image — that's exactly the desired behavior, but it does mean a full `--build` always loses any in-venv state. There is no in-venv state worth preserving in v1.

- **Anonymous volumes accumulate on disk.** Every `up` (without `down -v`) that recreates a container leaves an orphaned anonymous volume from the old container. `docker volume prune` clears them. Cosmetic disk pressure on long-iteration sessions; not functional.

- **Worker container behavior.** `worker` has no healthcheck, so the same defect was silently slowing every worker restart by a full re-sync. The same fix (`- /app/.venv`) eliminates the problem; verification is implicit (worker starts in seconds rather than minutes).

- **Subdirectory bind interaction.** `- .:/app` provides everything in the host project. The added `- /app/.venv` overlays just `.venv`. Other host subdirectories (`apps/`, `config/`, `tests/`, etc.) remain hot-reloadable. Verified by Compose semantics: explicit sub-mounts take precedence over the parent bind.

- **`docker compose up -d` (no `--build`) on a freshly-cloned repo.** First time the anonymous volume is created from the (possibly cached) image; no problem.

- **CI.** CI does NOT use docker compose; it uses uv + service containers. Fix does not touch CI.

- **Prod-style runs.** `docker compose -f docker-compose.yml up` (no override) is unaffected — no bind mount, no shadowing.

- **Spec/code drift.** §3.a now includes verbatim before/after for the spec example so the implementation prompt has zero room to introduce divergence between the file edit and the doc example.

---

## 7. Already-implemented work that must not regress

The fix must not modify or break:

- **All 41 spec deliverable files** as enumerated in `implementation_report.md` § "Files created" (plus `tests/test_settings.py`).
- **`Dockerfile`** — multi-stage uv build produces `/app/.venv` correctly; line 34 `PATH="/app/.venv/bin:${PATH}"`.
- **`docker-compose.yml`**'s six service definitions, healthchecks, depends_on chains, env_file, network, named volumes (`postgres_data`, `redis_data`, `qdrant_data`, `bge_cache`).
- **`apps/core/views.py:healthz`** — `@functools.lru_cache(maxsize=1)` lazy QdrantClient singleton, `concurrent.futures.ThreadPoolExecutor` 2 s timeouts, 401-vs-unreachable distinction, JSON shape, 200/503 status codes.
- **`apps/core/logging.py:configure_logging`** — structlog at import-time, JSON in prod / kv in dev, ContextVars wired (request_id/tenant_id/bot_id/doc_id), gunicorn.access/gunicorn.error loggers wired to ProcessorFormatter.
- **`config/settings.py`** — env-driven via django-environ, INSTALLED_APPS for all 6 apps, DRF AllowAny, QDRANT/BGE/SEARCH dicts, CELERY_*, `LOGGING_CONFIG = None`, configure_logging call at module bottom.
- **All AppConfig stubs** (`apps/<n>/apps.py`) with `name = "apps.<n>"`.
- **Empty `migrations/__init__.py`** in `apps/tenants` and `apps/documents`.
- **`tests/`** — `test_settings.py` SQLite overlay, `conftest.py` `_allow_testserver` fixture, `test_healthz.py` verbatim from spec (with `@pytest.mark.django_db`).
- **`scripts/verify_setup.py`** — exit codes 0/1; `[verify_setup] All checks passed.` on success, `[verify_setup] FAIL <subsystem>: ...` on failure.
- **`.github/workflows/ci.yml`** — `astral-sh/setup-uv@v5`, service containers, env block with `POSTGRES_HOST=localhost` / `QDRANT_HOST=localhost`.
- **`pyproject.toml`** including the `[[tool.uv.index]]` for pytorch-cpu (Phase 4 prep).
- **Acceptance criteria 1, 2, 3** which already PASS host-side. After the fix, criteria 5, 6, 7, 8, 10 should also PASS.
- **spec.md pitfalls #1–#5 and #7–#11** — only #6 is edited in place; numbering preserved.

The fix surface is intentionally narrow: two YAML lines added across `docker-compose.override.yml` (one for web, one for worker), and one spec.md pitfall rewrite + matching example update. Nothing else.
