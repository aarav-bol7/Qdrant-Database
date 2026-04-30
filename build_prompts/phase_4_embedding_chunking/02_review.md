# Phase 4 — Step 2 of 3: Review the Plan & Cover Missing Edge Cases

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to CRITIQUE the plan from Step 1 and revise it. Do not write production code, do not install dependencies, do not load the model.**

---

## Required reading (in this order)

1. `build_prompts/phase_4_embedding_chunking/spec.md` — source of truth.
2. `build_prompts/phase_4_embedding_chunking/plan.md` — the plan from Step 1.
3. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract (locked vector schema).
4. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
5. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.
6. `README.md` — context.

If `plan.md` does not exist, abort: `"Plan not found. Run PROMPT 1 first."`

---

## Your task

Adversarially review the plan. Find every gap, wrong assumption, missed edge case, and production-readiness flaw. Then produce a revised plan that addresses every finding.

Save outputs to:

- `build_prompts/phase_4_embedding_chunking/plan_review.md` — critique findings (NEW)
- `build_prompts/phase_4_embedding_chunking/plan.md` — overwritten with the revised plan

---

## Review lenses

For each lens, list findings (or `"no findings"`). Tag each: **[critical]** / **[major]** / **[minor]**.

### Lens 1 — Spec compliance

For every requirement in `spec.md`, verify the plan addresses it:

- All 9 deliverables (3 source + 3 tests + 3 modified)
- All 13 hard constraints (especially #2 FlagEmbedding-not-FastEmbed, #3 torch CPU-only, #5 one-pass embed, #6 chunk config, #11 lazy singleton)
- All 10 acceptance criteria
- All 10 common pitfalls
- The "Out of scope" list

Flag any requirement the plan misses.

### Lens 2 — Edge cases the plan missed

- **FlagEmbedding API signature drift.** The spec sketches `BGEM3FlagModel(model_name, use_fp16=, cache_dir=, device=)`. The actual installed version may have different kwarg names (e.g., `model_name_or_path` vs positional). Plan should include an "inspect FlagEmbedding signature" step before writing embedder.py.
- **`model.encode()` keyword arguments.** Same concern. Verify `return_dense`, `return_sparse`, `return_colbert_vecs` all exist.
- **Sparse output format.** FlagEmbedding's exact sparse output type may differ between versions: `dict[str, float]`, `dict[int, float]`, or a custom object. Plan should include an inspection step that prints `type(out["lexical_weights"][0])` and one entry.
- **ColBERT output format.** Likely an ndarray, but version-dependent. Plan should print `type` and `shape`.
- **`AutoTokenizer.from_pretrained(model_name, cache_dir=...)`** — does the tokenizer share BGE-M3's HuggingFace cache directory? If not, separate download. Plan should verify.
- **Chunker's `_CHARS_PER_TOKEN = 4` heuristic.** For Asian languages, ratio is closer to 1-2 chars per token. The hard truncation in `_truncate_to_max_tokens` corrects, but raises chunk count. Plan should accept this as v1 limitation.
- **`langchain_text_splitters` dep.** Was it added in Phase 1's pyproject.toml? Yes per Phase 1 spec. Verify the import path matches the installed version (`langchain_text_splitters` vs `langchain.text_splitter`).
- **`numpy` dep.** Comes transitively from torch + qdrant-client. Plan should verify it's importable.
- **`embedder.warmup()` function.** Spec includes it. Plan should explicitly cover it (called by `verify_setup.py --full`).
- **Test parallelism with model load.** If `pytest -n 4` runs 4 workers, each loads BGE-M3 (1.8 GB × 4 = 7.2 GB RAM). Plan should explicitly say "tests run sequentially" OR mark embedder tests with a worker affinity.
- **`tests/test_embedder.py` autouse fixture loads model on collection.** This means `pytest tests/test_chunker.py` (chunker only) doesn't load the model — fine. But `pytest tests/` loads model even before any embedder test runs. Verify autouse + scope=session interaction.
- **Stack rebuild without volume wipe.** `make rebuild` does `down -v` which DROPS the bge_cache volume. The next `up` re-downloads ~1.14 GB. Plan should distinguish `make restart` (no -v, preserves cache) from `make rebuild` (full reset).
- **`docker compose down -v` from the user's verification block** wipes bge_cache too. Plan should warn about the cost or use `down` (without -v) where data preservation matters.
- **First model download failure mid-test.** If the agent runs `pytest -m embedder` immediately after build with no network, the download fails and skips. Plan should run `make up && verify_setup.py --full` (which downloads + warms) BEFORE running embedder tests.

### Lens 3 — Production-readiness gaps

- **Memory pressure with 2 gunicorn workers + Celery worker + their own model copies.** 1.8 GB × 3 = 5.4 GB just for embedders. Plus Postgres, Redis, Qdrant. Total ~7 GB. The user has ≥ 8 GB. Tight but OK. Plan should flag the budget.
- **Model load latency on first request.** ~30-60s. If a real upload arrives at the freshly-started worker, the user-facing request hangs for that long. Mitigation: `verify_setup.py --full` warms up workers manually post-deploy. v1 deferral acceptable.
- **`fp16` numerical precision.** Tests use `np.allclose(rtol=1e-2, atol=1e-3)`. Plan should note this rtol — too loose hides actual bugs, too tight fails for legitimate fp16 noise.
- **Tokenizer can't handle pathological input** (e.g., 1 MB of repeating chars). May OOM the tokenizer. Plan should flag for Phase 5's input validation (limit content length per item).
- **Sparse vector with 0 entries.** If a chunk's text is "the the the" (all stopwords) BGE-M3 might produce an empty sparse dict. `sparse_to_qdrant` returns `{indices: [], values: []}`. Qdrant accepts empty sparse vectors? Plan should verify.
- **ColBERT for very long chunks.** A 600-token chunk → 600 ColBERT vectors (each 1024-dim). At 4 bytes per float (fp16: 2 bytes), that's 600 × 1024 × 2 = ~1.2 MB per chunk for ColBERT alone. 100 chunks per upload → 120 MB. Plan should flag this volume for Phase 5/8.
- **Logging cardinality.** Per-chunk debug logs in the chunker × hundreds of chunks per doc = noisy. Plan should keep chunker logs at INFO+ level by default.
- **`embedder.warmup()` is called from `verify_setup.py --full`.** What if the model is already loaded (via a prior call in the same process)? The lru_cache no-ops correctly — verified.

### Lens 4 — Pitfall coverage audit

For each of spec.md's 10 pitfalls:
1. Does the plan address it explicitly?
2. Does verification catch it?

Pitfalls 1, 2, 3, 5, 6 are about correctness (CUDA wheel, FastEmbed, lazy load, format conversion). Plan must verify each.

### Lens 5 — Sequencing & dependency correctness

Walk the plan's build steps. For each:
- Does it need anything from a later step?
- Could it be done earlier?
- If interrupted after this step, is the working dir coherent?

Critical sequencing:
- pyproject.toml + uv sync FIRST (must precede any FlagEmbedding import)
- Verify CPU-only torch BEFORE writing embedder.py (saves rebuild if wrong)
- Embedder before chunker (chunker imports count_tokens)
- Chunker before payload (payload imports Chunk dataclass)
- Tests after their corresponding modules
- Image rebuild after all source files exist
- verify_setup.py extension after embedder works

### Lens 6 — Verification command quality

- After lockfile regen: `grep '+cpu' uv.lock` is a strong check. Confirm it's in the plan.
- After embedder.py: import-only smoke (no model load). Strong, catches syntax errors without 60s wait.
- After chunker.py: pytest with mocked count_tokens. Strong, fast.
- After payload.py: pytest. Strong.
- After image rebuild: `docker compose exec web uv pip list | grep nvidia` should be empty. Confirms no CUDA.
- After verify_setup.py --full: exit code 0. Strong.

Replace any weak verifications.

### Lens 7 — Tooling correctness

- `uv add` syntax for adding the deps. With sources mapping to pytorch-cpu index, the right pattern is to edit pyproject.toml directly + run `uv sync`, NOT `uv add torch` (which might not honor the sources mapping immediately).
- `pytest -m embedder` and `pytest -m "not embedder"` — verify the marker is registered in pyproject.toml's `[tool.pytest.ini_options].markers`.
- The `make` targets exist and work for Phase 4's needs.

### Lens 8 — Risk register completeness

Risks the plan may have missed:

- **HuggingFace Hub rate limits.** First model download from HF could rate-limit on shared CI infra. v1 acceptable.
- **`safetensors` package required for fp16 model load.** Should come transitively from FlagEmbedding's deps. Verify.
- **Disk space for bge_cache volume.** 1.14 GB. If the host's /var/lib/docker is on a small partition, the build may fail. Plan should note disk requirement.
- **Image build cache invalidation.** Adding torch to deps invalidates the `RUN uv sync` cache layer in the Dockerfile. Full re-sync from network. ~5 minutes.
- **`numpy` version pin.** Torch 2.4 requires numpy 1.x or 2.x depending on build. Verify uv.lock resolves consistently.

---

## Output structure

### File 1: `plan_review.md` (NEW)

```markdown
# Phase 4 Plan Review

## Summary
- Total findings: <N>
- Severity breakdown: <X critical, Y major, Z minor>
- Plan accuracy: <%> spec compliance
- Recommendation: accept revised plan / re-plan / escalate to user

## Findings by lens

### Lens 1 — Spec compliance
1. **[severity] Title.** <description>. Where in plan: <section/line>. Fix: <action>.

### Lens 2 — Missed edge cases
...

### Lens 3 — Production-readiness gaps
...

### Lens 4 — Pitfall coverage audit
...

### Lens 5 — Sequencing & dependency correctness
...

### Lens 6 — Verification command quality
...

### Lens 7 — Tooling correctness
...

### Lens 8 — Risk register completeness
...

## Findings escalated to user
<entries that need user decision before Prompt 3 can run>
```

### File 2: `plan.md` (OVERWRITE)

Same 10-section structure. Add a section 0 at top: **"Revision notes"** — list what changed, with cross-references to plan_review.md finding numbers. Resolve all [critical] and [major] findings inline.

---

## What "done" looks like for this prompt

Output to chat:

1. Confirmation both files saved.
2. Severity breakdown.
3. Findings escalated to user (titles only).
4. Recommendation: ready for Prompt 3, or user must weigh in?

Then **stop**.
