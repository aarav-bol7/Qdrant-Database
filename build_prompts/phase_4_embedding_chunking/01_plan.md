# Phase 4 — Step 1 of 3: Produce an Implementation Plan

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to PLAN, not to write code. Do not create source files. Do not run `uv add` or `uv sync`. Do not modify any Phase 1/2/3 file.**

---

## Required reading (in this order)

1. `README.md` — project charter; understand where Phase 4 fits.
2. `build_prompts/phase_4_embedding_chunking/spec.md` — the full Phase 4 specification. **Source of truth. Read it twice.**
3. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract; the locked vector schema (dense 1024 + sparse bm25 IDF + ColBERT 1024-per-token) is what Phase 4's embedder must produce.
4. `build_prompts/phase_3_qdrant_layer/implementation_report.md` — confirms Phase 3 deliverables and any qdrant-client API specifics.
5. `build_prompts/phase_2_domain_models/spec.md` — `chunk_id` format `{doc_id}__i{item_index}__c{chunk_index}` is locked.
6. `build_prompts/phase_1_foundation/spec.md` — locked stack; the `[[tool.uv.index]] pytorch-cpu` block already exists.
7. `rag_system_guide.md` if present — §3 Step 4 has the BGE-M3 vector-type rationale.

If `phase_4_embedding_chunking/spec.md` does not exist, abort.

---

## Your task

Produce a structured implementation plan. Save it to:

```
build_prompts/phase_4_embedding_chunking/plan.md
```

Step 2 (`02_review.md`) will critique it; Step 3 (`03_implement.md`) will execute it.

---

## What the plan must contain

### 1. Plan summary

3–5 sentence executive summary at the top (write last). What's getting built? What's the riskiest part? How will the build verify itself?

### 2. Build order & dependency graph

Enumerate every file from spec.md's "Deliverables" tree. For each: path · what Phase 4 needs · dependencies · which build step.

Rough dependency order:
- pyproject.toml change comes FIRST (deps must be installed before importing FlagEmbedding)
- embedder.py SECOND (no dependencies on chunker or payload)
- chunker.py THIRD (depends on embedder.count_tokens)
- payload.py FOURTH (depends on chunker.Chunk dataclass)
- test_chunker.py + test_payload.py — pure unit tests, no model load
- test_embedder.py — integration, marked @pytest.mark.embedder
- verify_setup.py extension — depends on embedder
- Stack rebuild + smoke last

### 3. Build steps (sequenced)

A numbered list of 10–14 build steps. Each step:
- **Goal** (one sentence)
- **Files touched**
- **Verification command**
- **Rollback action**

The dependency-install step (`uv sync` after pyproject.toml change) should be early. The torch+cpu verification (no nvidia-* in `uv pip list`) should immediately follow. THEN write embedder.py — the agent can't even import FlagEmbedding without the deps installed.

### 4. Risk register

For each plausible failure mode: risk · likelihood · impact · mitigation · detection. Cover at minimum:

- **Torch resolves to CUDA wheel** (most likely if `[tool.uv.sources]` missing). Image bloats by ~2.5 GB. Verify via `uv.lock` grep for `+cpu` or `download.pytorch.org/whl/cpu`.
- **HuggingFace download blocked / no network** during first-run model fetch. ~1.14 GB download. Mitigation: bge_cache volume persists across rebuilds; document that first run requires network.
- **fp16 unsupported on user's CPU.** Older CPUs without AVX2 may fall back to fp32 silently — slower, more memory. Detection: `_get_model()` log message.
- **Tokenizer mismatch.** If chunker uses a different tokenizer than embedder, MAX_CHUNK_TOKENS gets mis-enforced. Mitigation: chunker imports `count_tokens` from embedder — single source of truth.
- **Sparse format string-vs-int.** FlagEmbedding emits `{"42": 0.9}` (string keys); Qdrant wants int indices. The `sparse_to_qdrant()` helper must convert; if forgotten, Phase 5 upserts fail.
- **ColBERT shape (tokens, 1024) vs Qdrant's `list[list[float]]`.** Conversion via `.tolist()`. Verify in test.
- **Model loads at module-import time** (e.g., the agent calls `_get_model()` outside a function for "convenience"). Breaks gunicorn fork. Mitigation: lru_cache on a function, only called inside other functions.
- **Tests load real model in CI when not intended.** Mitigation: `pytest -m "not embedder"` excludes them. Embedder tests skip-not-fail if model can't load.
- **Image size unexpectedly large** even with CPU torch. Cause: leftover layers from CUDA attempt. Mitigation: full image rebuild + size check.
- **`scripts/verify_setup.py --full` times out** if Qdrant is up but model download is slow. The agent should NOT add a timeout to the script — long first runs are expected.
- **Phase 1/2/3 regression** from the pyproject.toml change. Verify all prior tests still pass.

### 5. Verification checkpoints

8–12 pause-and-verify points with exact commands and expected outcomes:

- After `pyproject.toml` edit: `uv sync` succeeds; `uv.lock` updated.
- After lockfile regen: `grep -c '+cpu' uv.lock` returns ≥ 1; `grep -c 'nvidia' uv.lock` returns 0.
- After embedder.py: import smoke test (DOESN'T load the model — just verifies the module imports cleanly).
- After chunker.py: `uv run pytest tests/test_chunker.py -v` (with mocked count_tokens) green.
- After payload.py: `uv run pytest tests/test_payload.py -v` green.
- After embedder tests written: `uv run pytest -m embedder -v` either green or skipped gracefully (depending on whether model is downloaded yet).
- After image rebuild: `docker compose exec web uv pip list | grep -E '(torch|nvidia)'` shows torch with `+cpu`, no nvidia.
- After `make up`: `make health` returns green JSON.
- After `verify_setup.py --full` from container: exits 0; reports model loaded + 3 vectors.
- After full-suite run: `uv run pytest -v` (with embedder tests included or excluded depending on host model availability).
- Out-of-scope guard: `git status --short` shows ONLY expected files.

### 6. Spec ambiguities & open questions

5–10 entries. Things to scrutinize:

- The spec sketches `BGEM3FlagModel(model_name, use_fp16=..., cache_dir=..., device=...)`. Verify the actual FlagEmbedding constructor signature matches; older versions may have different parameter names.
- The spec uses `model.encode(..., return_dense=True, return_sparse=True, return_colbert_vecs=True)`. Verify these kwargs exist.
- Sparse output: spec says FlagEmbedding emits string keys. Some versions emit int keys directly. Test which one by inspecting one output.
- ColBERT output: 2D ndarray? List of arrays? Spec says ndarray. Verify.
- `transformers.AutoTokenizer.from_pretrained(...)` for the BGE-M3 tokenizer. Does it download a separate tokenizer bundle, or is it shared with the model's cache_dir?
- `RecursiveCharacterTextSplitter`'s `_CHARS_PER_TOKEN = 4` is a heuristic. For non-English content, ratios differ. Acceptable for v1; flag for Phase 8.
- The chunker's "merge tiny final chunk into prev" logic — what if the merged chunk would exceed MAX_CHUNK_TOKENS? Spec says "fall through and accept the small chunk." Verify this is the intent.
- `tests/test_embedder.py`'s `model_loadable` session fixture is `autouse=True` so it runs even if no test in the file is selected. If a user runs `pytest tests/test_embedder.py::TestSparseToQdrant -v`, the fixture still runs and loads the model. Is this correct? Could move some fixtures to function scope to avoid loading for tests that don't need it.
- `verify_setup.py --full` calls `roundtrip_qdrant_collection()` AND `warmup_embedder()`. If the first fails, the second still runs (no early-exit). Should we exit early on Qdrant failure? Probably no — the user wants to see ALL failures, not just the first.

### 7. Files deliberately NOT created / NOT modified

Echo the spec's "Out of scope" list in your own words. Add the explicit Phase 1/2/3 don't-touch list. Note that `apps/grpc_service/`, `apps/qdrant_core/` (other than the files already created in Phase 3), `apps/tenants/`, `apps/documents/`, `apps/core/`, `config/` stay untouched.

### 8. Acceptance-criteria mapping

For each of the 10 criteria in spec.md: criterion summary · which build step satisfies it · verification command · expected output.

### 9. Tooling commands cheat-sheet

```
# Verify torch is CPU-only
grep -c '+cpu' uv.lock                              # ≥ 1
grep -c 'nvidia' uv.lock                            # 0

# Inside container
docker compose exec web uv pip list | grep -i torch
docker compose exec web uv pip list | grep -iE 'nvidia|cuda'  # empty

# Standard
uv run pytest tests/test_chunker.py -v
uv run pytest tests/test_payload.py -v
uv run pytest -m embedder -v
uv run pytest -m "not embedder" -v
uv run pytest -v                                    # all tests
uv run ruff check .
uv run ruff format --check .

# Compose
make up
make health
docker compose exec web python scripts/verify_setup.py
docker compose exec web python scripts/verify_setup.py --full
docker compose exec web pytest -m embedder -v
```

### 10. Estimated effort

Per build step. Note that the IMAGE REBUILD with new torch + transformers deps will take 5–10 minutes the first time. First-time `verify_setup.py --full` adds ~60s for model download + load.

---

## Output format

Single markdown file at `build_prompts/phase_4_embedding_chunking/plan.md`. 400–700 lines.

---

## What "done" looks like for this prompt

Output to chat:

1. Confirmation that `plan.md` was created.
2. Total line count.
3. A 5-bullet summary of key sequencing decisions (especially: where torch+cpu verification goes, which steps require an image rebuild, where embedder tests run).
4. Spec ambiguities flagged in section 6 (titles only).

Then **stop**.
