# Phase 4 Plan Review

## Summary

- **Total findings:** 21
- **Severity breakdown:** 4 critical · 9 major · 8 minor
- **Plan accuracy (spec compliance):** ~88% — 13/13 hard constraints addressed (12 explicitly, 1 implicit), 10/10 acceptance criteria mapped, but 2 of the 10 common pitfalls (#2 FastEmbed-via-extras, #10 verify_setup-as-subprocess) lack explicit coverage; 1 hard constraint (#13 "no code comments") not stated.
- **Recommendation:** **accept revised plan** — all critical findings have non-controversial fixes that fold into the existing build steps without re-architecting. No findings need user adjudication; nothing escalated.

---

## Findings by lens

### Lens 1 — Spec compliance

1. **[critical] Hard constraint #13 ("no code comments") not stated in the plan.** The spec says "No code comments unless the spec or a non-obvious invariant justifies them." The plan inherits the spec'd source code which already follows this rule, but the plan never explicitly tells the implementing agent NOT to add commentary while transcribing. Fix: add an explicit "Comment policy" subsection to §3 Step 5 (and same note for Step 6, 7) that says "default zero comments — copy spec body as-is; only retain comments the spec already includes."

2. **[critical] Pitfall #2 ("FastEmbed sneaks in via qdrant-client extras") has no explicit verification step.** Phase 1's `qdrant-client` was added without the `[fastembed]` extra. The plan must verify this hasn't drifted. Where in plan: §3 Step 3 verification block lacks a `grep -i fastembed uv.lock` check. Fix: add to Step 3: `grep -ci fastembed uv.lock` returning 0; also `uv pip list | grep -i fastembed` returning empty. If FastEmbed is present, RAM doubles (it loads its own ONNX BGE-M3 alongside FlagEmbedding's PyTorch one).

3. **[critical] Pitfall #10 ("model loaded twice when tests + verify_setup both run") not addressed.** Spec warns: tests should NOT invoke `verify_setup.py` as a subprocess, because each subprocess re-loads the ~1.8 GB model. Plan §3 Step 13 runs both `verify_setup.py --full` and `pytest -m embedder` in sequence inside the SAME container — that's two separate Python processes, two model loads. This is intentional (different invocations), but the test code itself must never `subprocess.run(['python', 'scripts/verify_setup.py'])`. Fix: add an explicit guard in §6 ambiguities and a verification in Step 14 that `tests/test_embedder.py` does NOT contain `subprocess`, `os.system`, or `Popen`.

4. **[major] Hard constraint #5 ("one-pass embed produces all three vectors") not explicitly verified.** The plan's `embed_passages` calls `model.encode(..., return_dense=True, return_sparse=True, return_colbert_vecs=True)` — one call. But the plan never says "verify exactly one model.encode call per passage batch." Fix: add to §3 Step 5 verification: a regex grep on `embedder.py` for `model.encode` should return exactly 1 occurrence; and `test_embedder.py::TestEmbedPassages::test_returns_three_vector_types` already proves all three come back from one call (no separate calls needed).

5. **[minor] Hard constraint #9 ("version=1, is_active=True always written") not in build-step verification.** Implicit in `payload.py` per spec, but plan §3 Step 7 doesn't require a grep-confirmation. Fix: add to Step 7: `grep -c '"version": 1' apps/ingestion/payload.py` ≥ 1; `grep -c '"is_active": True' apps/ingestion/payload.py` ≥ 1.

6. **[minor] §8 acceptance-criteria mapping missing one detail for criterion 4.** Criterion 4 says "runs in under 5 seconds." Plan §8 row 4 says "All green; no model-load log" but doesn't mention the 5-second budget. Fix: replace expected output with "All green in < 5 s; absence of `bge_m3_loading` in stderr."

### Lens 2 — Missed edge cases

7. **[major] Memory budget for 2 gunicorn workers + 1 Celery worker = 3 model copies.** The Phase 1 Compose stack runs `gunicorn --workers 2` (2 forks, each with its own model after first request) plus the `worker` Celery container (1 process with its own model if it ever calls embed_*). 1.8 GB × 3 ≈ 5.4 GB just for embedders, plus Postgres + Redis + Qdrant + container overhead → ~7 GB minimum. Where in plan: §4 risk register doesn't include this. Fix: add a new risk **R17** "Memory budget at saturation" with mitigation "document the 7-GB minimum host RAM requirement; flag for Phase 8 to consider per-process model-sharing via `mmap` or shared-memory if memory pressure becomes an issue."

8. **[major] Model load order: tests vs. cache warm.** Running `pytest -m embedder -v` BEFORE `verify_setup.py --full` means the test fixture is the first to download the model. Network failure aborts the entire pytest run, marking it failed (not skipped — the autouse fixture does pytest.skip on exception, so actually it's skipped). Either way, the user expectation is "verify the build works" → the deterministic order is `verify_setup.py --full` first (downloads + warms), then `pytest -m embedder -v` (uses the cached model). Where in plan: §3 Step 13 lists both but doesn't enforce order. Fix: re-sequence Step 13 commands to put `verify_setup.py --full` BEFORE `pytest -m embedder -v` and document why.

9. **[major] FlagEmbedding's `import` alone may fetch torch.cuda module.** Some FlagEmbedding versions check `torch.cuda.is_available()` at import time. On a CPU-only torch wheel, `torch.cuda` exists but reports False — fine. But if the wheel was somehow CUDA, the import would succeed at the host level and the user wouldn't notice until embed time. Step 4's API inspection imports FlagEmbedding; that's a useful early-warning signal. Where in plan: §3 Step 4 doesn't say "this also doubles as an unintentional canary for CUDA-vs-CPU torch." Fix: add a note to Step 4: "If `import FlagEmbedding` segfaults or fails with a CUDA driver error, the torch wheel is wrong — re-do Step 3."

10. **[major] `_CHARS_PER_TOKEN = 4` heuristic poor for Asian languages.** For CJK/Thai content, ratio is closer to 1–2 chars per token. The chunker's pre-truncation char-count target may produce chunks with 2× the intended token count, then `_truncate_to_max_tokens` corrects — but every truncation is a content split that the splitter would have done at a more semantically meaningful boundary. Where in plan: §4 risk register lacks this. Fix: add a new risk **R18** "Non-English content over-chunks" with mitigation "accept v1 limitation; flag for Phase 8 — adjust `_CHARS_PER_TOKEN` per language or detect language and pick a per-language heuristic; document that the test uses English content where 4 chars/token is right."

11. **[major] `pytest -p no:cacheprovider` or worker affinity if user uses pytest-xdist.** The spec's autouse session fixture loads ONE model per pytest session. With `pytest -n 4` (xdist parallel workers), each worker is a separate process, each loading the model. Total 4 × 1.8 GB = 7.2 GB. Where in plan: not addressed. Fix: add a note to §6 ambiguities recommending "do NOT run embedder tests with `-n` parallelism" — for v1, the chunker/payload tests are fast enough that single-process pytest is fine. The plan does not have this issue today (no -n flag in any verification command), but it's worth flagging.

12. **[minor] `langchain_text_splitters` import path verification.** Older versions used `langchain.text_splitter.RecursiveCharacterTextSplitter`; the modular split puts it in `langchain_text_splitters` (separate package, this is what we want). Plan adds the right package but doesn't verify the import path post-install. Fix: add to §3 Step 3 verification: `uv run python -c "from langchain_text_splitters import RecursiveCharacterTextSplitter; print('ok')"`.

13. **[minor] `numpy` version range conflict.** Plan §4 R16 mentions this but with low likelihood. Numpy 2.x has been stable for ~18 months and torch 2.5+ supports it. Validate by checking torch's published wheels list at the time of build. Where in plan: §3 Step 3 has the right verification (uv sync succeeds), but doesn't mention what to do if it fails. Fix: extend R16's mitigation: "if uv resolver complains, pin numpy>=2.0,<3 explicitly in pyproject.toml as a temporary workaround."

14. **[minor] Stack rebuild costs the `bge_cache` volume only on `down -v` / `make rebuild`.** `make restart` (and `make down` then `make up`) preserves the volume, so the second model load is from cache. Plan §3 Step 12 says `make down; make up` — that's NOT `down -v`, so cache survives. But §10 doesn't make this distinction. Fix: add a sentence to §10 row 12 noting "second `make up` reuses the bge_cache volume; subsequent `--full` runs skip the 1.14 GB download."

### Lens 3 — Production-readiness gaps

15. **[major] Model load latency on first user request (~30–60 s).** With lazy `_get_model()`, the first POST `/v1/.../documents` after a fresh worker boot blocks the request for the full model load. This is a v1-acceptable design (warm via `verify_setup.py --full` post-deploy), but the plan doesn't flag it. Where in plan: §4 risk register doesn't include it. Fix: add risk **R19** "First-request latency on cold worker" — mitigation: "operators run `verify_setup.py --full` (or any embed call) post-deploy to warm; documented in Phase 8 runbook; v1-acceptable."

16. **[minor] fp16 numerical-precision tolerance (`rtol=1e-2, atol=1e-3`).** Plan inherits spec's `np.allclose(...)` test. This rtol is loose enough to pass legitimate fp16 nondeterminism but tight enough to catch real bugs. Plan should explicitly note this trade-off so a future maintainer doesn't tighten or loosen blindly. Where: §6 ambiguities. Fix: add ambiguity #16 with the trade-off explanation.

17. **[minor] Per-chunk DEBUG logs in chunker create cardinality.** With 100 chunks per upload × 100 uploads/day = 10,000 DEBUG lines/day from chunker alone. INFO-level upload logs are fine; DEBUG should stay out of prod. Where in plan: §4 risk register. Fix: add to risk register or §6 — note that `apps/ingestion/chunker.py` uses `logger.debug(...)` for per-item summary, which is gated by Phase 1's `DJANGO_LOG_LEVEL=INFO` default. No code change needed; just confirm the level filter works.

18. **[minor] Tokenizer pathological-input handling.** A 1 MB string of repeating chars may OOM the tokenizer or take > 30 s. Phase 5 will validate input size; Phase 4 trusts upstream. Where: §6 ambiguities. Fix: add ambiguity #17 — "Phase 4 doesn't bound `count_tokens` input size; Phase 5's validation must cap item content length (suggest 1 MB or 200K chars)."

### Lens 4 — Pitfall coverage audit

| # | Pitfall | Plan addresses? | Verification catches? |
|---|---|---|---|
| 1 | torch resolves to CUDA wheel | ✓ R1, V3+V4+V11 | ✓ grep `+cpu`, grep `nvidia` |
| 2 | FastEmbed sneaks via qdrant-client extras | ✗ **gap** (finding #2 above) | ✗ no `grep fastembed` |
| 3 | Model loads at import | ✓ R7, V6 | ✓ Step 5 import smoke is < 1 s |
| 4 | Tokenizer mismatch | ✓ R4 | partial — covered structurally; needs an integration assertion (finding #4) |
| 5 | Sparse format conversion | ✓ R5 | ✓ test in spec |
| 6 | ColBERT shape | ✓ R6 | ✓ test in spec |
| 7 | MIN_CHUNK_CHARS drops only chunk | ✓ §6 #14 | ✓ chunker test for short content |
| 8 | MAX_CHUNK_TOKENS not enforced post-split | ✓ §6 #7 | ✓ `_truncate_to_max_tokens` + test |
| 9 | Embedder tests in CI without bge_cache | ✓ R8 | ✓ skip-not-fail fixture |
| 10 | Model loaded twice (verify_setup as subprocess) | ✗ **gap** (finding #3 above) | ✗ no `grep subprocess` in test |

19. **[critical] Two pitfalls (#2, #10) lack explicit verification commands.** Same as findings #2 and #3 above; consolidated here for the audit perspective.

### Lens 5 — Sequencing & dependency correctness

Walking the plan steps:

- Step 1 (read) → no deps. ✓
- Step 2 (pyproject edit) → no deps. ✓
- Step 3 (uv sync) → deps Step 2. ✓ Critical guard: this is BEFORE writing any code so a wrong wheel doesn't waste later work.
- Step 4 (FlagEmbedding API inspection) → deps Step 3 (FlagEmbedding installed). ✓
- Step 5 (embedder.py) → deps Step 4. ✓
- Step 6 (chunker.py) → deps Step 5 (imports count_tokens). ✓
- Step 7 (payload.py) → deps Step 6 (imports Chunk). ✓
- Steps 8/9 (chunker + payload tests) → deps Step 6/7. ✓
- Step 10 (test_embedder.py) → deps Step 5. ✓
- Step 11 (verify_setup extension) → deps Step 5. ✓
- Step 12 (image rebuild) → deps Steps 2/3 (pyproject + lockfile). ✓
- Step 13 (e2e) → deps Step 12. ✓
- Step 14 (audit) → deps everything. ✓

20. **[minor] Step 13 ordering issue.** Same as finding #8: `verify_setup.py --full` should run before `pytest -m embedder -v` for cache warming. Re-ordered in revision.

If interrupted mid-step:

- After Step 2 but before Step 3: pyproject changed but lockfile stale. `uv run pytest` runs against the OLD venv (no torch yet) → all Phase 1/2/3 tests still pass. Coherent.
- After Step 3 but before Step 5: lockfile has torch but no embedder code. Phase 1/2/3 tests still green. Coherent.
- After Step 5 but before Step 6: chunker won't import (`from apps.ingestion.embedder import count_tokens` is fine because count_tokens exists). Actually that DOES work. The dependency is from chunker → embedder, not the other way. Even after embedder.py is written, importing chunker.py would fail because chunker.py doesn't exist. Coherent — only affects forward-progress, not regression.
- After Step 7 but before Step 12: tests run host-side; container is stale (no torch in image). Acceptable; rebuild deferred to Step 12.

### Lens 6 — Verification command quality

Strong points:
- V3+V4 (`grep '+cpu'` + `grep -ci nvidia`) — definitive yes/no.
- V6 (import smoke in < 1 s) — proves no eager model load.
- V9 (chunker+payload tests in < 5 s) — proves no model load and basic correctness.
- V11+V12 (in-container pip list + verify_setup) — final ground truth.

Weak points (all addressed in revision):
- V3 missing FastEmbed grep (finding #2).
- V8 doesn't say "Phase 2 grep-guard test must still pass" (`tests/test_naming.py::TestNoOtherCollectionNameConstructors`).
- V10 lacks a clear pass condition; "either green or skip-not-fail" is ambiguous if zero tests collect (which would happen if the marker is misregistered).

21. **[minor] V10 ambiguous pass condition.** Add: "either N tests pass OR N tests skip with `BGE-M3 cannot load`; collect-only must show ≥ 11 tests collected (not 0)."

### Lens 7 — Tooling correctness

- ✓ `uv add` not used; pyproject.toml edited directly (correct — `uv add torch` may not honor `[tool.uv.sources]` immediately).
- ✓ `pytest --strict-markers` is in `addopts` from Phase 1; the new `embedder` marker MUST be registered in `[tool.pytest.ini_options].markers` or `pytest -m embedder` errors.
- ✓ Make targets exist (`make up`, `make down`, `make health`, `make ps`, `make logs`, `make test`).
- Note: `make rebuild` from Phase 1 wraps `down -v + up --build` — that DROPS the `bge_cache` volume. Don't use `make rebuild` between Phase 4 verification runs unless you specifically want to invalidate the model cache.

### Lens 8 — Risk register completeness

The plan covers 16 risks. Findings 7, 10, 15 add R17, R18, R19. Other risks considered but rejected for Phase 4 scope:

- **HuggingFace Hub auth.** BGE-M3 is public; no auth needed. v1 acceptable.
- **safetensors version pin.** Should arrive transitively; plan §6 #11 covers verification.
- **Image build cache invalidation.** Addressed in plan §6 #12 (~5–10 min worst case for the layer rebuild).
- **Disk space for build context.** Phase 1's `.dockerignore` excludes the venv etc.; build context stays small.

---

## Findings escalated to user

**None.** All 21 findings have non-controversial fixes that the implementation agent can apply mechanically. The decision points (e.g., "early-exit on first failure in verify_setup --full" — plan §6 #9) preserve the existing Phase 1/3 idiom; if the user wants different semantics, that's a conscious change for a future spec revision, not a Phase 4 blocker.

The revised plan resolves all 4 critical and all 9 major findings inline. The 8 minor findings are folded in as plan refinements (clearer verification expected outputs, additional grep checks, additional risk-register entries).

---

## Revision recommendation

**Ready for Prompt 3** (the implementation step). The revised plan.md (overwritten in this prompt) addresses every critical and major finding. Minor findings are improvements to verification clarity; none of them block proceeding.

The implementing agent (Prompt 3) will read the revised plan.md, follow Steps 1–14 in order, and produce the implementation report.
