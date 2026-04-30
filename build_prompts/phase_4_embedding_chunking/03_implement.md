# Phase 4 — Step 3 of 3: Implement & Self-Review

> **You are a coding agent invoked from `/home/bol7/Documents/BOL7/Qdrant`.**
> **Your job in this prompt is to BUILD what the revised plan describes, then VERIFY it against the spec, then REPORT honestly.**

---

## Required reading (in this order)

1. `build_prompts/phase_4_embedding_chunking/spec.md` — re-read in full.
2. `build_prompts/phase_4_embedding_chunking/plan.md` — the revised plan from Step 2. Your roadmap.
3. `build_prompts/phase_4_embedding_chunking/plan_review.md` — the critique. Don't re-litigate.
4. `build_prompts/phase_3_qdrant_layer/spec.md` — Phase 3 contract (locked vector schema).
5. `build_prompts/phase_3_qdrant_layer/implementation_report.md` — Phase 3 outcomes.
6. `build_prompts/phase_2_domain_models/spec.md` — Phase 2 contract.
7. `build_prompts/phase_1_foundation/spec.md` — Phase 1 contract.
8. `README.md` — context.

If any of those is missing, abort.

---

## Hard rules during implementation

1. **Follow the revised plan.** Deviations must be justified in the final report.
2. **Build in the order the plan specifies.** Especially: dep install BEFORE writing embedder.py.
3. **Run the plan's verification commands at every checkpoint.** Don't accumulate broken state.
4. **Honor every "Out of scope" item.** No DRF serializers, no API views, no orchestration. Phase 4 ships pure Python modules.
5. **Do NOT modify Phase 1/2/3 files** except `pyproject.toml` (extension) and `scripts/verify_setup.py` (extension). Both are explicitly authorized.
6. **No code comments unless the spec or a non-obvious invariant justifies them.**
7. **Never commit `.env`.**
8. **Verify torch is CPU-only (`+cpu` in version, no `nvidia-*` packages).** This is the single most important production-readiness check in Phase 4.
9. **Verify the FlagEmbedding API matches the spec sketch BEFORE writing embedder.py in full.** Run `inspect.signature(BGEM3FlagModel.__init__)` and `inspect.signature(model.encode)` first.
10. **No emoji in code or comments. No `*.md` files beyond `implementation_report.md`.**

---

## Implementation phases

### Phase A — pyproject.toml extension + uv sync

Edit `pyproject.toml`:

1. Add `"FlagEmbedding>=1.3"` and `"torch>=2.4"` to `[project].dependencies`.
2. Add (or verify exists) the `[[tool.uv.index]]` block for `pytorch-cpu`.
3. Add the `[tool.uv.sources]` block mapping `torch = { index = "pytorch-cpu" }`.
4. Add the `embedder` marker to `[tool.pytest.ini_options].markers`.

Run:
```bash
uv sync
```

**Verify:**
```bash
grep -c '+cpu' uv.lock                      # ≥ 1
grep -c 'nvidia' uv.lock                    # 0
```

If `nvidia-*` packages appear, the `[tool.uv.sources]` mapping is wrong. Stop and fix.

### Phase B — FlagEmbedding API verification (pre-implementation)

Before writing embedder.py:

```bash
uv run python -c "
from FlagEmbedding import BGEM3FlagModel
from inspect import signature
print('BGEM3FlagModel.__init__:', signature(BGEM3FlagModel.__init__))
"
```

Document the actual signature in your implementation report. If it differs from the spec sketch, adapt while preserving the SEMANTICS (fp16, CPU, cache_dir).

Don't load the model yet — that's a 60s download/load. Save it for Phase F.

### Phase C — embedder.py

Create `apps/ingestion/embedder.py` per the spec, adjusting for any API differences from Phase B.

**Verify (no model load):**
```bash
uv run python -c "
from apps.ingestion.embedder import (
    DENSE_DIM, COLBERT_DIM,
    embed_passages, embed_query, count_tokens,
    sparse_to_qdrant, colbert_to_qdrant, warmup,
)
print('imports ok')
"
```

### Phase D — chunker.py

Create `apps/ingestion/chunker.py`.

**Verify (no model load via mocked count_tokens):**
```bash
uv run python -c "
from apps.ingestion.chunker import chunk_item, Chunk, CHUNK_CONFIG, MIN_CHUNK_CHARS, MAX_CHUNK_TOKENS
print('imports ok')
"
```

### Phase E — payload.py

Create `apps/ingestion/payload.py`.

**Verify:**
```bash
uv run python -c "
from apps.ingestion.payload import build_payload, build_chunk_id, ScrapedItem, ScrapedSource
print('imports ok')
"
```

### Phase F — Unit tests for chunker + payload

Create `tests/test_chunker.py` and `tests/test_payload.py`.

**Verify:**
```bash
uv run pytest tests/test_chunker.py tests/test_payload.py -v
```

All green. Both test files use mocks / pure logic — no real model load. Total runtime under 5 seconds.

### Phase G — Stack rebuild + image torch verification

```bash
make down
make up                                 # docker compose -f docker-compose.yml up -d --build
sleep 90                                # web container takes longer; first build pulls torch (~250 MB) + transformers
make ps                                 # all healthy
```

**Critical verification — torch is CPU-only inside the container:**
```bash
docker compose -f docker-compose.yml exec web uv pip list | grep -iE 'torch|nvidia|cuda'
```

Expected output: torch with `+cpu` suffix (e.g., `torch 2.4.x+cpu`). NO lines containing `nvidia-`, `cuda-`, etc. If any nvidia/cuda package appears, abort and fix the `[tool.uv.sources]` mapping.

**Also verify image size is in expected range:**
```bash
docker images qdrant-web --format '{{.Size}}'
```
Expected: ~1.5–2 GB (up from ~700 MB pre-Phase-4). NOT 3+ GB (which would mean CUDA torch).

### Phase H — Embedder integration test (loads real model)

```bash
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
```

First run: 60–90s (model download + load + Qdrant round-trip). Subsequent runs: ~10s (cached model).

Expected output:
```
[verify_setup --full] Creating collection ...
[verify_setup --full] Round-trip succeeded.
[verify_setup --full] Loading BGE-M3 (this may take ~30-60s) ...
[verify_setup --full] Embedder OK. dense=1024 sparse_keys=<N> colbert_tokens=<M>
[verify_setup] All checks passed.
```

Then run the marked tests inside the container:
```bash
docker compose -f docker-compose.yml exec web pytest -m embedder -v
```

All green.

### Phase I — Test from host (skip-not-fail if model unavailable)

```bash
uv run pytest -m embedder -v
```

If the host has the bge_cache mounted (which it doesn't unless you copy it from Docker), the model isn't available. Tests should skip with a clear message — NOT fail.

```bash
uv run pytest -m "not embedder" -v        # skip slow tests
```

All non-embedder tests must be green.

### Phase J — Full suite + regression

```bash
uv run pytest -v                                # all tests; embedder tests skip on host if needed
uv run ruff check .
uv run ruff format --check .

# Phase 1 + 2 + 3 regression
make health                                     # Phase 1: 200 + green JSON
docker compose -f docker-compose.yml exec web pytest tests/test_qdrant_collection.py -v   # Phase 3
docker compose -f docker-compose.yml exec web pytest tests/test_models.py tests/test_naming.py -v   # Phase 2

# Out-of-scope guard
git status --short
```

Files in `git status --short` must be:
- `pyproject.toml` (modified)
- `uv.lock` (regenerated)
- `apps/ingestion/embedder.py` (new)
- `apps/ingestion/chunker.py` (new)
- `apps/ingestion/payload.py` (new)
- `scripts/verify_setup.py` (modified)
- `tests/test_embedder.py` (new)
- `tests/test_chunker.py` (new)
- `tests/test_payload.py` (new)
- `build_prompts/phase_4_embedding_chunking/implementation_report.md` (new)

ANY other file in the diff is a deviation requiring justification.

---

## Self-review

After Phase J passes, run this self-review against the **spec**.

For each acceptance criterion in `spec.md` (all 10), record:
- **Pass / fail** (be honest)
- **Verification command run**
- **Output observed**

For each pitfall (all 10), record:
- **Avoided / hit / not-applicable**
- **How confirmed**

For each "Out of scope" item, confirm not implemented.

---

## Final report

Save to `build_prompts/phase_4_embedding_chunking/implementation_report.md`. Structure:

```markdown
# Phase 4 — Implementation Report

## Status
**OVERALL:** PASS / FAIL / PARTIAL

## Summary
- Files created: <N>
- Files modified outside Phase 4 scope: <N> (must be ≤ 2; pyproject.toml + scripts/verify_setup.py)
- Tests added: <N>
- Tests passing: <N>/<N>
- Acceptance criteria passing: <N>/10
- Final web image size: <MB>
- BGE-M3 first load time: <seconds>

## FlagEmbedding API verification (Phase B)
[Paste the output of the inspection. Note any deviations from the spec sketch.]

## Torch CPU-only verification
- `grep '+cpu' uv.lock` count: <N>
- `grep 'nvidia' uv.lock` count: <N> (must be 0)
- Inside container: `uv pip list | grep -iE 'torch|nvidia|cuda'` output:
  ```
  <paste>
  ```
  (must show only torch with +cpu, no nvidia/cuda packages)

## Acceptance criteria
[for all 10: result, command, output, notes]

## Pitfall avoidance
[for all 10: avoided/hit/N/A, how confirmed]

## Out-of-scope confirmation
[brief list]

## Phase 1 + Phase 2 + Phase 3 regression check
- Phase 1: /healthz still green: <output>
- Phase 2: tests/test_models.py + tests/test_naming.py still green: <output>
- Phase 3: tests/test_qdrant_collection.py still green inside container: <output>
- No prior-phase file modified except pyproject.toml + verify_setup.py: paste `git diff --name-only` output

## Deviations from plan
[for each deviation: what · why · impact]

## FlagEmbedding API deviations from spec sketch
[any places where the actual library API required different syntax]

## Spec defects discovered
[anything in spec.md that turned out to be incorrect, contradictory, or impossible]

## Outstanding issues
[non-blocking but worth knowing before Phase 5]

## Files created or modified
[clean tree]

## Image size delta
- Before Phase 4: <MB>
- After Phase 4: <MB>
- Delta: <MB>

## Commands to verify the build (one block, copy-pasteable)

```bash
make down
make up
sleep 90
make ps
make health
docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full
docker compose -f docker-compose.yml exec web pytest -m embedder -v
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

## Verdict
One paragraph: is Phase 4 ready to ship? Next step?
```

---

## What "done" looks like for this prompt

Output to chat:

1. Path to `implementation_report.md`.
2. **Overall status: PASS / FAIL / PARTIAL.**
3. Acceptance criteria score: `X/10 passed`.
4. Torch CPU-only verification: confirmed / FAILED.
5. FlagEmbedding API deviations summary (one line).
6. Phase 1+2+3 regression status (PASS / FAIL).
7. Recommended next step.

Then **stop**.

---

## A note on honesty

If torch resolved to the CUDA wheel and the image is 3.5 GB, say so. If FlagEmbedding's API doesn't match the spec, document it. If embedder tests skip on the host but pass in the container, flag it explicitly. The report is the contract — write it to be true, not flattering.
