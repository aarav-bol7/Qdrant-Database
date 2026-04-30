# Phase 4 — Implementation Plan (REVISED)

> Audience: the implementation agent (Prompt 3 of 3). Read end-to-end before touching any file. Phase 4 builds the embedding + chunking layer on top of verified-green Phase 1/2/3 at `/home/bol7/Documents/BOL7/Qdrant`.

---

## 0. Revision notes

This is the post-review plan. Revisions vs. the original plan, with cross-references to `plan_review.md`:

| # | Section affected | Change | Resolves |
|---|---|---|---|
| 1 | §3 Step 5/6/7 | Added explicit "Comment policy: zero comments outside what the spec already includes" | Finding 1 (critical, hard constraint #13) |
| 2 | §3 Step 3 verification + §4 R1 | Added `grep -ci fastembed uv.lock` (must be 0) and `uv pip list \| grep -i fastembed` (must be empty) | Finding 2 (critical, pitfall #2) |
| 3 | §3 Step 14 + §6 #18 | Added grep guard: `tests/test_embedder.py` must NOT contain `subprocess`/`os.system`/`Popen` | Finding 3 (critical, pitfall #10) |
| 4 | §3 Step 5 | Added `grep -c 'model.encode' apps/ingestion/embedder.py` must equal 1 | Finding 4 (major, hard constraint #5) |
| 5 | §3 Step 7 | Added `grep -c '"version": 1'` and `grep -c '"is_active": True'` confirmations | Finding 5 (minor, hard constraint #9) |
| 6 | §8 row 4 | Tightened expected output: "All green in < 5 s; absence of `bge_m3_loading` in stderr" | Finding 6 (minor) |
| 7 | §4 R17 (new) | Added: 7-GB host RAM minimum (2 web + 1 celery + 1 model each = 5.4 GB embedders + infra) | Finding 7 (major) |
| 8 | §3 Step 13 | Re-ordered: `verify_setup.py --full` BEFORE `pytest -m embedder` so cache warms first | Finding 8 (major) |
| 9 | §3 Step 4 | Added note: `import FlagEmbedding` doubles as CUDA-vs-CPU canary | Finding 9 (major) |
| 10 | §4 R18 (new) | Added: `_CHARS_PER_TOKEN=4` heuristic over-chunks non-English; v1-acceptable | Finding 10 (major) |
| 11 | §6 #19 (new) | Added: do NOT run embedder tests with `pytest -n` (xdist) — multiplies model copies | Finding 11 (major) |
| 12 | §3 Step 3 | Added: `python -c "from langchain_text_splitters import RecursiveCharacterTextSplitter; print('ok')"` | Finding 12 (minor) |
| 13 | §4 R16 | Extended mitigation: pin `numpy>=2.0,<3` if uv resolver fails | Finding 13 (minor) |
| 14 | §10 row 12 | Added: `make down; make up` keeps `bge_cache` volume; second `--full` skips download | Finding 14 (minor) |
| 15 | §4 R19 (new) | Added: cold-worker first-request latency ~30–60 s; warm via verify_setup post-deploy | Finding 15 (major) |
| 16 | §6 #20 (new) | Added: fp16 rtol=1e-2 trade-off explicitly documented | Finding 16 (minor) |
| 17 | §6 #21 (new) | Added: chunker DEBUG log cardinality — gated by `DJANGO_LOG_LEVEL=INFO` | Finding 17 (minor) |
| 18 | §6 #22 (new) | Added: tokenizer pathological-input handling deferred to Phase 5 input validation | Finding 18 (minor) |
| 19 | §3 Step 3 | Strengthened V10 expected output: "≥ 11 tests collected; either all pass OR all skip with explicit message" | Finding 21 (minor) |

All 4 critical and 9 major findings resolved inline. 8 minor findings folded into verification clarity.

---

## 1. Plan summary

Phase 4 wraps BAAI's BGE-M3 model (via `FlagEmbedding`) and `langchain_text_splitters.RecursiveCharacterTextSplitter` in three pure-Python modules under `apps/ingestion/`, plus a 20-field Qdrant payload builder. The riskiest part is the `pyproject.toml` change: getting torch resolved against the existing `[[tool.uv.index]] pytorch-cpu` block prevents a 2 GB CUDA wheel from bloating the image, and the `qdrant-client` dep must NOT acquire the `[fastembed]` extra (which would double RAM by loading a parallel ONNX BGE-M3). The build verifies itself in three places — (a) `grep '+cpu' uv.lock` + `grep -ci 'nvidia\|cuda\|fastembed' uv.lock` after lockfile regen, (b) host-side `pytest tests/test_chunker.py tests/test_payload.py` for fast unit verification with no model load, and (c) `verify_setup.py --full` inside a freshly-built container, which downloads the ~1.14 GB BGE-M3 weights once into the persistent `bge_cache` volume and confirms all three vector types come back at correct dims (dense=1024, sparse=non-empty dict, ColBERT inner-axis=1024). Phase 4 ships pure modules; nothing wired to HTTP/gRPC yet (Phase 5 owns that).

---

## 2. Build order & dependency graph

### Files to create / modify

| # | Path | What Phase 4 needs | Depends on | Build step |
|---|---|---|---|---|
| 1 | `pyproject.toml` (EXTEND) | Add `FlagEmbedding>=1.3`, `torch>=2.4`, `langchain-text-splitters>=0.3` to `[project].dependencies`; add `[tool.uv.sources] torch = { index = "pytorch-cpu" }`; register `embedder` pytest marker. | Phase 1's existing `[[tool.uv.index]] pytorch-cpu` block. | Step 2 |
| 2 | `uv.lock` (REGENERATE) | `uv sync` regenerates; verify torch resolves to a `+cpu` wheel; verify NO `fastembed`, `nvidia-*`, `cuda-*`. | (1) | Step 3 |
| 3 | `apps/ingestion/embedder.py` (NEW) | BGE-M3 wrapper, `lru_cache` model+tokenizer singletons, `count_tokens`, `embed_passages`, `embed_query`, `sparse_to_qdrant`, `colbert_to_qdrant`, `warmup`, `DENSE_DIM=COLBERT_DIM=1024`. | (2) — needs `FlagEmbedding`, `transformers`, `torch`, `numpy`, `django.conf.settings.BGE` (Phase 1). | Step 5 |
| 4 | `apps/ingestion/chunker.py` (NEW) | `chunk_item(content, *, source_type, item_index) -> list[Chunk]`, per-source-type `CHUNK_CONFIG`, `Chunk` dataclass, `_truncate_to_max_tokens`, tiny-tail merge logic. Imports `count_tokens` from `embedder`. | (2), (3) | Step 6 |
| 5 | `apps/ingestion/payload.py` (NEW) | `ScrapedSource`/`ScrapedItem` frozen dataclasses, `build_chunk_id`, `build_payload` (20 fields, `version=1`, `is_active=True`). Imports `Chunk` from `chunker`. | (4) | Step 7 |
| 6 | `tests/test_chunker.py` (NEW) | Pure unit tests with mocked `count_tokens`. Verifies indices, source-type routing, MAX_CHUNK_TOKENS enforcement, tiny-tail merge, fallback for splitter dropping everything. | (4) | Step 8 |
| 7 | `tests/test_payload.py` (NEW) | Pure unit tests; no embedder load. Verifies field shape, ISO8601 `uploaded_at`, `source_url` fallback, list-copy of `section_path`, custom-metadata pass-through. | (5) | Step 9 |
| 8 | `tests/test_embedder.py` (NEW) | Integration tests; loads real BGE-M3 once via `scope="session", autouse=True` skip-not-fail fixture. Marked `@pytest.mark.embedder`. **MUST NOT** contain `subprocess`/`os.system`/`Popen` (would re-load model). | (3) | Step 10 |
| 9 | `scripts/verify_setup.py` (EXTEND) | Add `_warmup_embedder()` returning `(bool, str)` per the existing Phase 1+3 idiom. Add the `--full` branch that calls it AFTER `_roundtrip_qdrant_collection()`. | (3), (8) | Step 11 |

### Acyclic dependency graph

```
pyproject.toml ──► uv.lock ──► embedder.py ──► chunker.py ──► payload.py
                                    │             │              │
                                    │             ▼              ▼
                                    │      test_chunker.py   test_payload.py
                                    │
                                    ├─► test_embedder.py
                                    └─► verify_setup.py (extension)
```

The chunker imports `count_tokens` from `embedder` as a function reference; calling it triggers `_get_tokenizer()` only when chunks are actually built. Test code mocks the import to avoid the real model load.

---

## 3. Build steps (sequenced)

Fourteen numbered steps. Each has **goal**, **files**, **verification**, **rollback**.

### Step 1 — Read & inventory

- **Goal:** confirm Phase 1/2/3 state on disk; capture mtimes of locked files for the post-build don't-touch audit.
- **Files touched:** none.
- **Verification:**
  ```bash
  ls apps/ingestion/                          # apps.py + __init__.py only
  ls apps/qdrant_core/                        # client/collection/exceptions/naming + apps.py + __init__.py
  ls tests/                                   # conftest, test_settings, test_healthz, test_models, test_naming, test_qdrant_client, test_qdrant_collection
  grep -n 'pytorch-cpu' pyproject.toml        # the [[tool.uv.index]] block exists
  grep -nE 'FlagEmbedding|^.+torch>=|langchain' pyproject.toml  # nothing yet
  ```
- **Rollback:** N/A (read-only).

### Step 2 — Edit `pyproject.toml`

- **Goal:** add the three production deps + `[tool.uv.sources]` mapping + `embedder` pytest marker.
- **Files touched:** `pyproject.toml`.
- **Three edits in one tool call:**
  1. In `[project].dependencies`, insert (alphabetical position with the other deps): `"FlagEmbedding>=1.3",`, `"langchain-text-splitters>=0.3",`, `"torch>=2.4",`.
  2. Below the existing `[[tool.uv.index]] pytorch-cpu` block, add:
     ```toml
     [tool.uv.sources]
     torch = { index = "pytorch-cpu" }
     ```
  3. In `[tool.pytest.ini_options]`, add a `markers = [...]` entry:
     ```toml
     markers = [
         "embedder: tests that require the BGE-M3 model to be loaded (slow, ~30s+ first run)",
     ]
     ```
- **Verification:**
  ```bash
  grep -nE 'FlagEmbedding|langchain-text-splitters|^.*torch>=' pyproject.toml   # 3 matches
  grep -n 'tool.uv.sources' pyproject.toml                                       # 1 match
  grep -n 'embedder:' pyproject.toml                                             # 1 match
  ```
- **Rollback:** revert pyproject.toml.

### Step 3 — Regenerate `uv.lock` and verify wheel hygiene

- **Goal:** pull the new wheels, lock them, and prove torch is CPU-only AND fastembed is absent BEFORE writing any embedder code.
- **Files touched:** `uv.lock`.
- **Commands:**
  ```bash
  uv sync                                                  # regenerates lockfile + venv
  grep -c '+cpu' uv.lock                                   # ≥ 1 (torch wheel suffix)
  grep -cE 'download\.pytorch\.org/whl/cpu' uv.lock        # ≥ 1 (custom index URL)
  grep -ciE 'nvidia|cu(da|d)' uv.lock                      # 0 (no CUDA bits)
  grep -ci 'fastembed' uv.lock                             # 0 (no FastEmbed sneak-in)
  uv pip list | grep -iE 'torch|FlagEmbedding|transformers|langchain-text-splitters'
  uv pip list | grep -iE 'nvidia|cu(da|d)'                 # empty
  uv pip list | grep -i 'fastembed'                        # empty
  uv run python -c "from langchain_text_splitters import RecursiveCharacterTextSplitter; print('ok')"
  ```
- **Expected outcomes:**
  - `torch` shown as `2.x.y+cpu`.
  - `transformers`, `tokenizers`, `safetensors`, `huggingface-hub` arrived transitively (via FlagEmbedding).
  - `numpy` already present (numpy 2.4.4 from Phase 1's qdrant-client tree); may be re-resolved if torch demands.
  - `langchain-text-splitters` installed; import succeeds.
  - No `nvidia-*`, no `cuda-*`, no `cudnn-*`, no `fastembed`.
- **If verification fails:** the `[tool.uv.sources]` block is wrong/absent OR `qdrant-client` somehow acquired `[fastembed]`. Re-edit pyproject.toml and re-run `uv sync`. Do NOT proceed with a CUDA wheel or fastembed in the lockfile.
- **Rollback:** revert pyproject.toml + uv.lock.

### Step 4 — Inspect FlagEmbedding API surface (no production write)

- **Goal:** verify the actual installed signatures match the spec sketch. 30-second exploration that prevents a guaranteed late-step failure. **Note:** `import FlagEmbedding` doubles as a canary — if the torch wheel is wrong (CUDA, missing libs), the import fails or segfaults here, before any code is written.
- **Files touched:** none.
- **Commands:**
  ```bash
  uv run python -c "
  import inspect
  from FlagEmbedding import BGEM3FlagModel
  print('ctor:', inspect.signature(BGEM3FlagModel.__init__))
  print('encode:', inspect.signature(BGEM3FlagModel.encode))
  "
  uv run python -c "
  import inspect
  from langchain_text_splitters import RecursiveCharacterTextSplitter
  print('splitter ctor:', inspect.signature(RecursiveCharacterTextSplitter.__init__))
  "
  uv run python -c "
  import inspect, transformers
  print('AutoTokenizer.from_pretrained:', inspect.signature(transformers.AutoTokenizer.from_pretrained))
  "
  ```
- **What to capture:**
  - `BGEM3FlagModel.__init__` — first positional arg name (`model_name` vs `model_name_or_path`), presence of `use_fp16`, `cache_dir`, `device`.
  - `BGEM3FlagModel.encode` — kwargs `return_dense`, `return_sparse`, `return_colbert_vecs`, `batch_size`, `max_length`.
  - `RecursiveCharacterTextSplitter.__init__` — kwargs `chunk_size`, `chunk_overlap`, `length_function`, `separators`.
  - `AutoTokenizer.from_pretrained` — `cache_dir` arg.
- **If signatures differ from spec sketch**, adapt `embedder.py`'s `_get_model()` body during Step 5 to use the actual kwarg names while preserving SEMANTICS (CPU device, fp16, custom cache_dir).
- **Rollback:** N/A.

### Step 5 — Write `apps/ingestion/embedder.py`

- **Goal:** ship the BGE-M3 wrapper. Code from spec §"File-by-file specification → embedder.py", adapted to actual signatures from Step 4. No model load at import time.
- **Files touched:** `apps/ingestion/embedder.py`.
- **Comment policy:** zero comments outside what the spec already includes. The spec body has a top-level docstring + a few in-function strings; preserve those, add nothing else.
- **Verification (import-only smoke; does NOT load the model):**
  ```bash
  time uv run python -c "
  import django, os
  os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  django.setup()
  from apps.ingestion import embedder
  assert embedder.DENSE_DIM == 1024
  assert embedder.COLBERT_DIM == 1024
  assert all(callable(getattr(embedder, name)) for name in (
      'embed_passages','embed_query','count_tokens',
      'sparse_to_qdrant','colbert_to_qdrant','warmup',
  ))
  print('embedder import smoke: OK')
  "
  # Hard constraint #5 verification: exactly one model.encode call
  test "$(grep -c 'model\.encode' apps/ingestion/embedder.py)" -eq 1 && echo "single-encode OK" || echo "MULTI-ENCODE BUG"

  uv run ruff check apps/ingestion/embedder.py
  uv run ruff format --check apps/ingestion/embedder.py
  ```
- **Expected:** import smoke completes in < 1 s real time. If > 5 s, the model is being loaded at import — find and remove the eager call.
- **Rollback:** delete the file.

### Step 6 — Write `apps/ingestion/chunker.py`

- **Goal:** wraps `RecursiveCharacterTextSplitter` with per-source-type sizing. Imports `count_tokens` from embedder (function reference; not invoked at import).
- **Files touched:** `apps/ingestion/chunker.py`.
- **Comment policy:** zero comments outside what the spec already includes.
- **Verification:**
  ```bash
  uv run python -c "
  import django, os
  os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  django.setup()
  from apps.ingestion.chunker import (
      CHUNK_CONFIG, DEFAULT_CHUNK_CONFIG,
      MIN_CHUNK_CHARS, MAX_CHUNK_TOKENS,
      Chunk, chunk_item,
  )
  assert MIN_CHUNK_CHARS == 50
  assert MAX_CHUNK_TOKENS == 600
  assert set(CHUNK_CONFIG) == {'pdf','docx','url','html','csv','faq','image'}
  print('chunker import smoke: OK')
  "
  uv run ruff check apps/ingestion/chunker.py
  ```
- **Rollback:** delete the file.

### Step 7 — Write `apps/ingestion/payload.py`

- **Goal:** the 20-field payload builder + `build_chunk_id`, `ScrapedSource`/`ScrapedItem` dataclasses.
- **Files touched:** `apps/ingestion/payload.py`.
- **Comment policy:** zero comments outside what the spec already includes.
- **Verification:**
  ```bash
  uv run python -c "
  from apps.ingestion.payload import build_chunk_id, ScrapedItem, ScrapedSource
  assert build_chunk_id('doc-abc', 0, 0) == 'doc-abc__i0__c0'
  assert build_chunk_id('doc-abc', 3, 12) == 'doc-abc__i3__c12'
  s = ScrapedSource(type='pdf'); i = ScrapedItem(item_index=0)
  print('payload import smoke: OK')
  "
  # Locked invariants (hard constraint #9):
  test "$(grep -c '"version": 1' apps/ingestion/payload.py)" -ge 1 && echo "version=1 locked"
  test "$(grep -c '"is_active": True' apps/ingestion/payload.py)" -ge 1 && echo "is_active=True locked"
  uv run ruff check apps/ingestion/payload.py
  # Phase 2 grep-guard regression: ensure no f"t_*__b_" outside naming.py
  uv run pytest tests/test_naming.py::TestNoOtherCollectionNameConstructors -v
  ```
- **Rollback:** delete the file.

### Step 8 — Write `tests/test_chunker.py`; run it

- **Goal:** pure unit tests with `count_tokens` mocked to `len(text) // 4`. Fast (~1 s).
- **Files touched:** `tests/test_chunker.py`.
- **Verification:**
  ```bash
  uv run pytest tests/test_chunker.py -v 2> /tmp/test_chunker.stderr
  test ! -s /tmp/test_chunker.stderr || ! grep -q 'bge_m3_loading' /tmp/test_chunker.stderr   # no model load
  ```
- **Rollback:** delete the file.

### Step 9 — Write `tests/test_payload.py`; run it

- **Goal:** unit tests for the payload builder. No mocks needed.
- **Files touched:** `tests/test_payload.py`.
- **Verification:**
  ```bash
  uv run pytest tests/test_payload.py -v
  ```
- **Rollback:** delete the file.

### Step 10 — Write `tests/test_embedder.py`

- **Goal:** integration tests behind the `embedder` marker. Skip-not-fail if model can't load.
- **Files touched:** `tests/test_embedder.py`.
- **Verification:**
  ```bash
  # Static lint + format
  uv run ruff check tests/test_embedder.py
  # Pitfall #10 guard — never spawn a subprocess that re-loads the model
  ! grep -nE '^[^#]*\bsubprocess\b|os\.system|Popen' tests/test_embedder.py
  # Collection sanity — ≥ 11 tests should be discovered
  uv run pytest --collect-only -m embedder 2>&1 | tail -20
  # Active run (host-side; will skip if model unavailable)
  uv run pytest -m embedder -v 2>&1 | tail -40
  ```
  Expected: `≥ 11 tests collected`; either all pass (cached model) OR all skip with `BGE-M3 cannot load in this environment` message. NEVER fail.
- **Rollback:** delete the file.

### Step 11 — Extend `scripts/verify_setup.py`

- **Goal:** add `_warmup_embedder()` per the Phase 1/3 `(bool, str)` idiom (matches Phase 3's `_roundtrip_qdrant_collection`). Wire it into `--full` AFTER the round-trip.
- **Files touched:** `scripts/verify_setup.py`.
- **Function shape (preserves existing return-tuple convention):**
  ```python
  def _warmup_embedder() -> tuple[bool, str]:
      try:
          import os
          os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
          import django
          django.setup()
          from apps.ingestion.embedder import COLBERT_DIM, DENSE_DIM, embed_passages
          print("[verify_setup --full] Loading BGE-M3 (this may take ~30-60s) ...")
          out = embed_passages(["A short sentence to verify embeddings produce all three vectors."])
          dense, sparse, colbert = out["dense"][0], out["sparse"][0], out["colbert"][0]
          if len(dense) != DENSE_DIM:
              return False, f"dense dim mismatch: got {len(dense)}, want {DENSE_DIM}"
          if not isinstance(sparse, dict) or not sparse:
              return False, f"sparse must be non-empty dict, got {type(sparse).__name__}"
          colbert_inner = colbert.shape[1] if hasattr(colbert, "shape") else (
              len(colbert[0]) if colbert else 0
          )
          if colbert_inner != COLBERT_DIM:
              return False, f"colbert inner dim: got {colbert_inner}, want {COLBERT_DIM}"
          print(
              f"[verify_setup --full] Embedder OK. dense={len(dense)} "
              f"sparse_keys={len(sparse)} colbert_tokens={len(colbert)}"
          )
      except Exception as exc:
          return False, f"{type(exc).__name__}: {exc}"
      return True, "ok"
  ```
- **Wire-up in `main()`:** after the existing `if args.full: rt_ok, rt_msg = _roundtrip_qdrant_collection()` block, add:
  ```python
      we_ok, we_msg = _warmup_embedder()
      if not we_ok:
          print(f"[verify_setup] FAIL embedder: {we_msg}", file=sys.stderr)
          return 1
  ```
- **No timeout enforcement** — first model download is slow (and the operator should tolerate it, per spec pitfall #10 reverse).
- **Verification:**
  ```bash
  uv run python -c "
  import importlib.util
  spec = importlib.util.spec_from_file_location('verify_setup', 'scripts/verify_setup.py')
  mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
  assert callable(mod._warmup_embedder)
  print('verify_setup extension import OK')
  "
  uv run ruff check scripts/verify_setup.py
  ```
  End-to-end execution deferred to Step 13.
- **Rollback:** restore from Phase 3 baseline.

### Step 12 — Rebuild the Docker image

- **Goal:** the new `pyproject.toml` invalidates the Dockerfile's `uv sync` cache layer. Rebuild from scratch. Pulls torch (~110 MB CPU wheel) + transformers (~7 MB) + tokenizers + safetensors + huggingface-hub. **5–10 min on cold uv wheel cache; 1–2 min if warm.**
- **Files touched:** none (build only).
- **Commands (NOTE: `make down` keeps volumes; `make rebuild` would do `down -v` and wipe `bge_cache`. Use plain down/up to preserve cache between runs):**
  ```bash
  make down                   # stops stack, KEEPS volumes
  make up                     # docker compose -f docker-compose.yml up -d --build
  sleep 90                    # web container takes longer this build (torch + transformers download)
  make ps
  make health
  ```
- **Verification:**
  ```bash
  make health
  # Expected: {"status":"ok","version":"0.1.0-dev","components":{"postgres":"ok","qdrant":"ok"}}
  docker compose exec web uv pip list | grep -iE '(^torch|^FlagEmbedding|transformers|tokenizers|langchain)'
  docker compose exec web uv pip list | grep -iE 'nvidia|cuda'      # MUST be empty
  docker compose exec web uv pip list | grep -i 'fastembed'         # MUST be empty
  docker compose exec web uv pip show torch | grep -i 'Version'     # +cpu suffix
  docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -i 'qdrant'
  # Image size: ~1.5–2 GB expected; > 3 GB indicates CUDA torch slipped through
  ```
- **Rollback:** `make down`; revert pyproject.toml + uv.lock; `make up` rebuilds prior image.
- **Host-CLI fallback:** if docker daemon socket permission is denied (Phase 3 outstanding §1), run host-side `QDRANT_HOST=localhost uv run pytest -m embedder -v` to exercise the same code against the same Qdrant instance.

### Step 13 — End-to-end verification (`verify_setup.py --full` + embedder pytest)

- **Goal:** prove all three vector types come back at correct dims from a freshly-built container, against the real Qdrant. Order matters: `verify_setup.py --full` runs FIRST so `bge_cache` warms before pytest touches the model.
- **Files touched:** none.
- **Commands (re-ordered per finding #8: warm-then-test):**
  ```bash
  # 1. Warm the cache: download + load + Qdrant round-trip + 1 embed call
  docker compose exec web python scripts/verify_setup.py            # default fast checks
  docker compose exec web python scripts/verify_setup.py --full     # ~60–90 s first run; ~10 s subsequently

  # 2. Now pytest can use the cached model
  docker compose exec web pytest -m embedder -v                     # 11 tests, fast after warmup
  docker compose exec web pytest -m "not embedder" -v               # all prior tests + chunker + payload
  docker compose exec web pytest -v                                 # full suite as last sanity
  ```
- **Expected `--full` output:**
  ```
  [verify_setup --full] Creating collection for tenant='verify_<ts>', bot='rt0' ...
  [verify_setup --full] Upserted dummy point. Deleting by doc_id ...
  [verify_setup --full] Round-trip succeeded.
  [verify_setup --full] Dropping test collection ...
  [verify_setup --full] Loading BGE-M3 (this may take ~30-60s) ...
  [verify_setup --full] Embedder OK. dense=1024 sparse_keys=<int> colbert_tokens=<int>
  [verify_setup] All checks passed.
  ```
- **Host-equivalent fallback:** `QDRANT_HOST=localhost uv run pytest -m embedder -v` from host (re-downloads model into `~/.cache/huggingface` separate from container's `bge_cache` volume).
- **Rollback:** N/A — verification only.

### Step 14 — Don't-touch audit + final regression sweep

- **Goal:** confirm no Phase 1/2/3 file's content was modified outside the two authorized extensions; full regression green.
- **Files touched:** none.
- **Commands:**
  ```bash
  # No subprocess / model re-load in test_embedder.py (pitfall #10 guard)
  ! grep -nE '^[^#]*\bsubprocess\b|os\.system|Popen' tests/test_embedder.py

  # Authorized changes: pyproject.toml, uv.lock, scripts/verify_setup.py only.
  # New files: apps/ingestion/{embedder,chunker,payload}.py + tests/test_{embedder,chunker,payload}.py + plan/review/report markdown.

  uv run pytest -v                             # full host-side suite
  uv run ruff check .
  uv run ruff format --check .
  uv run python manage.py makemigrations --check --dry-run    # Phase 2 regression: no new migrations
  curl -fsS http://localhost:8080/healthz | python -m json.tool
  ```
- **Don't-touch list (paths whose content must be byte-identical to Phase 3):**
  - `apps/core/{views,logging,urls,apps,__init__}.py`
  - `apps/tenants/{models,admin,validators,apps,__init__}.py` + `migrations/0001_initial.py`
  - `apps/documents/{models,admin,apps,__init__}.py` + `migrations/0001_initial.py`
  - `apps/qdrant_core/{client,collection,exceptions,naming,apps,__init__}.py`
  - `apps/grpc_service/{apps,__init__}.py`
  - `apps/ingestion/{apps,__init__}.py`
  - `config/{settings,urls,wsgi,asgi,celery,__init__}.py`
  - `tests/{conftest,test_settings,test_healthz,test_models,test_naming,test_qdrant_client,test_qdrant_collection,__init__}.py`
  - `Dockerfile`, `docker-compose.yml`, `docker-compose.override.yml`, `Makefile`, `manage.py`, `.env.example`, `.gitignore`, `.dockerignore`, `.python-version`, `README.md`, `proto/.gitkeep`, `scripts/compile_proto.sh`, `.github/workflows/ci.yml`.
- **Rollback:** if any of the above shows a content change, revert it.

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation | Detection |
|---|---|---|---|---|---|
| R1 | torch resolves to CUDA wheel; OR fastembed sneaks in via qdrant-client extras | High if `[tool.uv.sources]` is omitted; Low otherwise | Image bloats (CUDA: 2 GB; FastEmbed: 1+ GB ONNX); deploys break on small disks | Add `[tool.uv.sources]`; verify `[fastembed]` extra absent | `grep '+cpu'` ≥1, `grep -ci 'nvidia\|cuda\|fastembed' uv.lock` = 0; `uv pip list | grep -i fastembed` empty |
| R2 | HuggingFace Hub blocks first model download | Medium (rate limits, network) | `--full` and embedder tests skip-not-fail | Document network requirement; `bge_cache` volume persists | `_get_model()` raises; fixture pytest.skips with captured exception |
| R3 | fp16 unsupported on user's CPU (no AVX-512) | Low (FlagEmbedding falls back in software) | ~2× slower embed; double RAM | Trust fallback; log `use_fp16=True` in `_get_model` | Wall-clock comparison; `bge_m3_loaded` log entries |
| R4 | Tokenizer mismatch chunker↔embedder | Low (single source of truth) | Chunks exceed 8192 tokens at encode; truncation loses content | `_truncate_to_max_tokens` uses embedder's tokenizer; both go through same `_get_tokenizer()` cache | Integration assertion in `test_embedder.py` |
| R5 | Sparse string keys vs Qdrant int indices | High if `sparse_to_qdrant` bypassed | Phase 5 upsert raises type error | `sparse_to_qdrant()` does the cast | `test_embedder.py::TestSparseToQdrant::test_converts_keys_to_int_indices` |
| R6 | ColBERT shape (n_tokens,1024) vs `list[list[float]]` | Same as R5 | Phase 5 upsert fails | `colbert_to_qdrant()` does `.tolist()` | `test_embedder.py::TestColbertToQdrant::test_converts_ndarray_to_list_of_lists` |
| R7 | Model loaded at module import time | Medium (easy mistake) | gunicorn master loads model BEFORE forking → 3× memory + grpc fork hazards | `_get_model()` is `@lru_cache(maxsize=1)`-decorated, called only inside other functions | Step 5 import smoke must complete in < 1 s |
| R8 | Tests load real model in CI without `bge_cache` | Medium | Test job times out / fails on download | CI defaults to `pytest -m "not embedder"`; embedder tests run only in dedicated cached pipeline | Session fixture skip-not-fails |
| R9 | Image size unexpectedly large after rebuild | Low | Slow deploys, disk pressure | Pin torch CPU index; verify lockfile; confirm no `nvidia-*` | `docker images` size < 2 GB |
| R10 | `verify_setup.py --full` times out on first run | Medium | Spurious failure in deploy gates | Do NOT add wall-clock timeout; long first runs expected | If anyone adds a timeout, this risk fires |
| R11 | Phase 1/2/3 regression from `pyproject.toml` change | Low | Healthz, models, qdrant tests fail | Run full suite + `make health` after rebuild | `uv run pytest -v`; `make health` |
| R12 | `langchain-text-splitters` missing from spec's deps table | High (spec literal omission) | Runtime ImportError when chunker.py runs | Plan adds it explicitly in Step 2 | `uv pip list \| grep langchain` after Step 3 |
| R13 | Sparse vector with empty dict (all stopwords) | Low | Qdrant rejects empty sparse vector | `sparse_to_qdrant({})` returns empty form; flag for Phase 5 to log warning | `test_embedder.py::TestSparseToQdrant::test_empty_input` |
| R14 | ColBERT volume per chunk (~1.2 MB) | High (locked schema) | Qdrant disk pressure at scale | Phase 4 doesn't change schema; flag for Phase 8 monitoring | Phase 8 metrics |
| R15 | Disk space for `bge_cache` (~1.14 GB) + image cache | Medium on small partitions | First `make up` fails with `no space left` | Document: ~3 GB free disk needed | `df -h /var/lib/docker` before Step 12 |
| R16 | Torch 2.x version pin clashes with numpy 2.x | Low | uv.lock won't resolve | If resolver fails, pin `numpy>=2.0,<3` in pyproject.toml as workaround | Step 3 sync error message |
| R17 | Memory budget at saturation (gunicorn x2 + celery x1 = 3 model copies × 1.8 GB) | Medium (depends on workload) | OOM kills under load | Document 7-GB host RAM minimum; flag for Phase 8 metrics + per-process model-share | `docker stats` during embed |
| R18 | Non-English content over-chunks (`_CHARS_PER_TOKEN=4` heuristic poor for CJK) | Medium (depends on tenant content) | More chunks than necessary; quality OK; cost up | Accept v1 limitation; flag for Phase 8 — per-language heuristic | Compare chunk counts on English vs CJK content |
| R19 | First-request latency on cold worker (~30–60 s) | High (first request after worker boot) | User-facing request hangs | Operators run `verify_setup.py --full` post-deploy to warm; documented in runbook (Phase 8) | Latency metric on first request after worker restart |

---

## 5. Verification checkpoints

| # | Where | Command | Expected |
|---|---|---|---|
| V1 | After Step 2 | `grep -nE 'FlagEmbedding\|langchain-text-splitters\|^.+torch>=' pyproject.toml` | 3 lines, all in `[project].dependencies` |
| V2 | After Step 2 | `grep -A2 'tool.uv.sources' pyproject.toml` | `torch = { index = "pytorch-cpu" }` |
| V3 | After Step 3 | `grep -c '+cpu' uv.lock`; `grep -ciE 'nvidia\|cuda\|fastembed' uv.lock` | `≥ 1` then `0` |
| V4 | After Step 3 | `uv pip list \| grep -iE 'torch\|FlagEmbedding\|langchain'` | 3+ lines, torch shows `+cpu` |
| V5 | After Step 5 | Import smoke from §3 Step 5 | OK in < 1 s; no model-load log |
| V6 | After Step 5 | `grep -c 'model\.encode' apps/ingestion/embedder.py` | exactly 1 (one-pass embed) |
| V7 | After Step 6 | Import smoke + ruff | OK and clean |
| V8 | After Step 7 | Import smoke + ruff + Phase 2 grep-guard `pytest tests/test_naming.py -v` | All green |
| V9 | After Step 8/9 | `uv run pytest tests/test_chunker.py tests/test_payload.py -v` | All green in < 5 s; `bge_m3_loading` absent from stderr |
| V10 | After Step 10 | `pytest --collect-only -m embedder` then `pytest -m embedder -v` | ≥ 11 tests collected; either all pass OR all skip with `BGE-M3 cannot load` |
| V11 | After Step 12 | `make health`; `docker compose exec web uv pip list \| grep -iE 'nvidia\|cuda\|fastembed'`; `uv pip show torch` | health green; no nvidia/cuda/fastembed; torch +cpu |
| V12 | After Step 13 | `verify_setup.py --full`; full pytest in container; `curl /healthz` | All exit 0; suite green; healthz green |

---

## 6. Spec ambiguities & open questions

1. **Spec's deps table omits `langchain-text-splitters`.** Spec defect; plan adds it in Step 2.
2. **`BGEM3FlagModel` constructor signature.** Step 4 inspects; Step 5 adapts.
3. **`model.encode(...)` return-key names.** Spec uses `dense_vecs`/`lexical_weights`/`colbert_vecs`. Step 4 verifies.
4. **Sparse output: string keys vs int keys.** `sparse_to_qdrant()` handles both via `int(token_id)` cast.
5. **ColBERT output: ndarray vs list-of-arrays.** `colbert_to_qdrant()` handles both via isinstance + `.tolist()` fallback.
6. **Tokenizer cache_dir behavior.** May or may not share BGE-M3 snapshot. Acceptable either way.
7. **Tiny-tail-merge overflow.** Spec says "fall through and accept the small chunk" — preserve content over chunk-size purity.
8. **`test_embedder.py` autouse session fixture.** Loads model even when running a single test that doesn't need it. Acceptable for v1.
9. **`verify_setup.py --full` early-exit on roundtrip failure.** Plan keeps Phase 1/3 idiom (early-exit on first failure). If user wants accumulate-and-report-all, that's a future spec change.
10. **`embedder` pytest marker registration.** Step 2 adds it; `--strict-markers` in addopts means an unregistered marker errors.
11. **`safetensors` transitive dep.** Step 3 verifies via `uv pip list`.
12. **Image-rebuild post-pyproject-edit.** Cache layer invalidated; full re-sync. ~5–10 min worst case.
13. **Docker socket permission outstanding from Phase 1/3.** Host-equivalent fallback (`QDRANT_HOST=localhost uv run pytest`) covers this. Documented in Step 12/13.
14. **Chunker fallback when splitter drops everything.** Spec includes "single whole-content chunk" fallback; tests cover.
15. **`embedder.warmup()` idempotency.** `lru_cache(maxsize=1)` makes repeats no-op.
16. **fp16 numerical-precision tolerance (`rtol=1e-2, atol=1e-3`).** Loose enough for fp16 noise; tight enough to catch real bugs. Trade-off accepted.
17. **Tokenizer pathological input.** `count_tokens("a"*10**6)` may take seconds and consume RAM. Phase 4 trusts upstream; Phase 5 must cap input size (~1 MB or ~200K chars per item).
18. **Pitfall #10 — `tests/test_embedder.py` MUST NOT spawn subprocesses.** Verified by grep guard in Step 14.
19. **Do NOT run embedder tests with `pytest -n` (xdist parallelism).** Each worker is a separate process and would multiply model copies. v1 single-process pytest only.
20. **`make rebuild` wipes `bge_cache`.** Use `make down; make up` (no `-v`) between Phase 4 verification runs to preserve the cache.
21. **Per-chunk DEBUG logs in chunker.** Gated by Phase 1's `DJANGO_LOG_LEVEL=INFO` default. No code change needed; just confirm the level filter works in practice.
22. **CUDA canary on `import FlagEmbedding`.** If torch wheel is wrong, the import fails at Step 4 — early warning before any embedder code is written.

---

## 7. Files deliberately NOT created / NOT modified

In addition to the don't-touch list in §3 Step 14:

- **No HTTP / DRF code.** No new `urls.py`, no views, no serializers. `apps/documents/views.py` does not get created in Phase 4.
- **No Qdrant upsert code.** No call to `client.upsert(...)` from `apps/ingestion/*` (Phase 5 owns the upload pipeline).
- **No gRPC code.** `proto/` is untouched.
- **No middleware.** `request_id`/`tenant_id`/`bot_id` contextvars (Phase 5/7).
- **No Celery tasks.** Phase 4 builds synchronous primitives.
- **No pipeline orchestrator.** `apps/ingestion/pipeline.py` is Phase 5.
- **No Postgres advisory-lock acquisition.** Phase 5.
- **No quantization, no atomic version swap, no audit log.**
- **`apps/ingestion/__init__.py` and `apps/ingestion/apps.py`** stay as Phase 1 stubs — UNCHANGED.

---

## 8. Acceptance-criteria mapping

| # | Criterion (summary) | Build step | Verification | Expected |
|---|---|---|---|---|
| 1 | `uv sync` regenerates lockfile with torch `+cpu`, no fastembed | Step 3 | `grep -c '+cpu' uv.lock; grep -ciE 'nvidia\|cuda\|fastembed' uv.lock` | `≥ 1` then `0` |
| 2 | `uv run ruff check .` zero violations | Steps 5–11 + 14 | `uv run ruff check .` | `All checks passed!` |
| 3 | `uv run ruff format --check .` passes | Same | `uv run ruff format --check .` | `N files already formatted` |
| 4 | Fast subset green in < 5 s | Steps 8 + 9 | `uv run pytest tests/test_chunker.py tests/test_payload.py -v` | All green; absence of `bge_m3_loading` in stderr |
| 5 | `pytest -m embedder -v` passes or skips clearly | Step 10 | `uv run pytest -m embedder -v` | Either green or skips with `BGE-M3 cannot load` |
| 6 | `docker compose up -d --build` brings stack up green | Step 12 | `make up; sleep 90; make health` | Green JSON |
| 7 | In-container `uv pip list` shows `+cpu` torch and no `nvidia-*` | Step 12 | `docker compose exec web uv pip list \| grep -iE 'torch\|nvidia\|cuda\|fastembed'` | torch with `+cpu`, others empty |
| 8 | `verify_setup.py --full` exits 0 | Step 13 | `docker compose exec web python scripts/verify_setup.py --full` | `[verify_setup] All checks passed.` |
| 9 | Embedder tests in container green | Step 13 | `docker compose exec web pytest -m embedder -v` | All green |
| 10 | Full host-side suite green; healthz green | Step 14 | `uv run pytest -v && curl -fsS http://localhost:8080/healthz \| python -m json.tool` | All green; `{"status":"ok",...}` |

If docker-CLI permission outstanding (Phase 3 §1): criteria 6/7/8/9 satisfy via host-equivalent path (`QDRANT_HOST=localhost`).

---

## 9. Tooling commands cheat-sheet

```bash
# ── Phase 4 setup ──
# Step 2 (manual edit): pyproject.toml gets FlagEmbedding/torch/langchain-text-splitters + [tool.uv.sources] + embedder marker

uv sync
grep -c '+cpu' uv.lock                                       # ≥ 1
grep -cE 'download\.pytorch\.org/whl/cpu' uv.lock            # ≥ 1
grep -ciE 'nvidia|cuda|fastembed' uv.lock                    # 0

# Step 4 — API inspection (no model load)
uv run python -c "import inspect; from FlagEmbedding import BGEM3FlagModel; print(inspect.signature(BGEM3FlagModel.__init__))"
uv run python -c "import inspect; from FlagEmbedding import BGEM3FlagModel; print(inspect.signature(BGEM3FlagModel.encode))"

# Steps 5–7 — module import smoke (no model load)
uv run python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup(); from apps.ingestion import embedder; print('ok')"
uv run python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup(); from apps.ingestion.chunker import chunk_item, MAX_CHUNK_TOKENS; print('ok')"
uv run python -c "from apps.ingestion.payload import build_chunk_id; print(build_chunk_id('d',0,0))"

# Quality gates
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run

# Tests (host-side; embedder tests skip if model can't load)
uv run pytest tests/test_chunker.py tests/test_payload.py -v   # < 5 s, no model load
uv run pytest -m embedder -v                                   # may skip
uv run pytest -m "not embedder" -v                             # 56+ tests, no model
uv run pytest -v                                               # full suite

# Docker stack (Step 12)
make down                                          # KEEPS volumes (no -v)
make up                                            # docker compose -f docker-compose.yml up -d --build
sleep 90
make ps
make health
docker compose exec web uv pip list | grep -iE '^torch|FlagEmbedding|transformers|tokenizers|langchain'
docker compose exec web uv pip list | grep -iE 'nvidia|cuda|fastembed'   # all empty

# Step 13 — warm-then-test
docker compose exec web python scripts/verify_setup.py --full   # ~60–90 s first run
docker compose exec web pytest -m embedder -v                   # post-warm
docker compose exec web pytest -v                               # full suite

# Host-equivalent fallback if docker CLI is locked down
QDRANT_HOST=localhost uv run pytest tests/test_qdrant_collection.py -v   # Phase 3 regression
QDRANT_HOST=localhost uv run pytest -m embedder -v                       # Phase 4 e2e
```

---

## 10. Estimated effort

| Step | Task | Effort | Notes |
|---|---|---|---|
| 1 | Read & inventory | 5 min | |
| 2 | pyproject.toml edits | 5 min | |
| 3 | `uv sync` + verify | 3–8 min | Network-bound |
| 4 | Inspect FlagEmbedding API | 5 min | |
| 5 | Write `embedder.py` | 15 min | ~150 lines |
| 6 | Write `chunker.py` | 15 min | ~110 lines |
| 7 | Write `payload.py` | 10 min | ~70 lines |
| 8 | Write `tests/test_chunker.py` | 15 min | ~80 lines |
| 9 | Write `tests/test_payload.py` | 10 min | ~120 lines |
| 10 | Write `tests/test_embedder.py` | 15 min | ~120 lines |
| 11 | Extend `verify_setup.py` | 10 min | |
| 12 | Image rebuild | **5–10 min** | Worst-case full network pull |
| 13 | E2E verify | **10–15 min** | First run downloads ~1.14 GB |
| 14 | Don't-touch audit + final sweep | 5 min | |
| | **Total** | **~2 hours** wall clock first run; **~1 hour** on warm cache; **~3 min** after volume re-use |

---

## End of plan (revised)

Phase 5 (Upload API) consumes this layer via:
```python
from apps.ingestion.chunker import chunk_item
from apps.ingestion.embedder import embed_passages, sparse_to_qdrant, colbert_to_qdrant
from apps.ingestion.payload import build_payload, ScrapedItem, ScrapedSource
from apps.qdrant_core.collection import get_or_create_collection
from apps.qdrant_core.client import get_qdrant_client
```
