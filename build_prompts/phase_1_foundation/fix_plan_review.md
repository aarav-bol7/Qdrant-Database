# Fix Plan Review

## Summary
- **Total findings:** 6
- **Severity breakdown:** 0 critical · 0 major · 6 minor
- **Recommendation:** **accept revised plan**. Diagnosis is sound, choice is well-reasoned, scope is correctly narrow. The minor findings tighten verification (proving the fix worked rather than that something happened) and lock down spec/code drift.

---

## Findings by lens

### Lens 1 — Root cause confirmation

1. **[minor] Diagnosis is theoretically sound but not empirically locked.** Plan §1 inspects `docker-compose.override.yml` (bind mount present, no `.venv` escape) and `Dockerfile` (venv at `/app/.venv`, `PATH` set), then reasons forward to "host's `.venv` shadows image's, foreign Python interpreter triggers re-sync". That chain is consistent with the observed `Downloading <pkg>` flood, but the plan never inspects the host's `.venv/pyvenv.cfg` to confirm the interpreter path is actually foreign. Where: `fix_plan.md` §1 "Root cause statement". **How to fix:** add one preflight check to §4 (verification) before any container action — `head -3 .venv/pyvenv.cfg` should show `home = /home/bol7/...` style host path; that proves the shadow is real. Counter-explanations to consider but reject: (a) `uv sync --frozen` in Dockerfile failed and fell back to non-frozen — would have left an incomplete venv but not produce the same `Downloading` log signature on every container start; (b) uv version mismatch host (0.9.28) vs container (`:latest` ≈ 29.x era) — possible cofactor but the dominant cause is the bind shadow, since prod-style runs (no override) work fine.

### Lens 2 — Anonymous vs named volume trade-offs

2. **[minor] Restart-vs-recreate gotcha not surfaced in the new pitfall #6 wording.** Plan §3.b's rewrite mentions `down -v && up -d --build` as the recipe after `pyproject.toml` change. But it doesn't explain *why* `docker compose restart` is insufficient: anonymous volumes survive `restart` and `stop`/`start` (the container is reused, the volume re-attaches). Where: `fix_plan.md` §3.b "After:" pitfall text. **How to fix:** in the pitfall update, add one sentence — "Plain `docker compose restart` keeps the old venv volume; only container recreation (via `up --build` or `down`/`up`) drops the anonymous volume."

### Lens 3 — Symmetry across services

No findings. Plan §3 file 1 explicitly applies the fix to both `web` and `worker`. §6 risks correctly identifies worker's silent slow-startup symptom and notes the same fix resolves it.

### Lens 4 — Spec compliance after fix

3. **[minor] Spec example block update needs an exact-line specification, not "match the file edit".** Plan §3.a says "add the `- /app/.venv` line in both blocks, immediately after `- .:/app`. Match the file edit exactly so spec doc and code don't drift." That's correct intent but leaves the implementation prompt freedom to misalign. The spec is a contract; drift is exactly what the wording guards against. Where: `fix_plan.md` §3.a. **How to fix:** in §3.a, write out the exact "before" and "after" YAML for the spec example (mirroring the file edit in §3.1 verbatim), so the next prompt has zero room to introduce a discrepancy. Also note that `spec.md` already grew a pitfall #11 (the SQLite-overlay note) since the original Phase 1 implementation; the fix must not re-number existing pitfalls when rewriting #6 — it edits in place.

### Lens 5 — Hot-reload regression risk

No findings. Plan §6 risks (subdirectory bind interaction) correctly notes Compose semantics: explicit sub-mounts overlay the parent bind. Hot-reload of `apps/`, `config/`, `tests/` is preserved because the bind on `/app` still surfaces those subtrees; only `/app/.venv` is overlaid.

### Lens 6 — Out-of-scope guard

No findings. Plan stays disciplined — exactly two code files (override + spec.md), `docker-compose.yml` left untouched, no Python edits, no Dockerfile edits, no test refactoring, no CI changes. §5 "Out of scope" enumerates the no-go zones.

### Lens 7 — Verification command quality

4. **[minor] Negative-check on `Downloading` lines is "optionally" suggested; should be required.** Plan §4 command 3 says "Optionally pipe through `grep -c '^Downloading' || echo 0` to assert absence." This is the *only* command that discriminates between "venv intact" and "venv re-synced quickly". It must be a required, exit-coded check, not optional. Where: `fix_plan.md` §4 command 3. **How to fix:** make the grep an explicit assertion with expected output `0`; if non-zero, the fix did not work and the build is still broken (just hiding it better).

5. **[minor] No command directly proves `/app/.venv` is the image's venv (not the host's).** Even with anonymous volume in place, an edge case is possible where the volume initialised from a stale layer or empty path. Verification should include a single positive proof. Where: `fix_plan.md` §4. **How to fix:** add a command — `docker compose exec web sh -c 'head -3 /app/.venv/pyvenv.cfg'` should show `home = /usr/local/bin` (or similar container Python path), NOT `/home/bol7/...`. Also `docker compose exec web sh -c 'which python && python -c "import django; print(django.get_version())"'` proves the resolved Python is the container's and Django imports without a re-sync round-trip.

### Lens 8 — Already-working invariants

6. **[minor] No explicit `pytest` re-run in verification plan.** Plan §7 says "Test suite still green" as an invariant but §4 doesn't run pytest after the fix. Pytest runs host-side and is unaffected by Compose changes, so it should still pass — but a 2-second sanity check is cheap insurance against accidental edits. Where: `fix_plan.md` §4. **How to fix:** append a final command — `uv run pytest -q` → expected `1 passed`.

---

## Findings escalated to user

**None.** All six findings are minor and have unambiguous mechanical fixes. No taste judgements, no missing information.

The revised plan is ready for the implementation prompt (Prompt 3).
