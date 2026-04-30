# Phase 4 — Embedding & Chunking

> **Audience:** A coding agent (e.g. Claude Code) building on top of verified-green Phase 1, 2, and 3 at `/home/bol7/Documents/BOL7/Qdrant`. Do not modify Phase 1/2/3 deliverables except where this spec explicitly says so.

---

## Mission

Build the embedding + chunking layer the upload pipeline consumes:

- **Embedder** at `apps/ingestion/embedder.py` — wraps FlagEmbedding's `BGEM3FlagModel`. Lazy module-level singleton. `fp16` precision. CPU-only. Exposes `embed_passages(texts)`, `embed_query(text)`, `count_tokens(text)`, `sparse_to_qdrant(...)`, `colbert_to_qdrant(...)`, plus dimension constants.
- **Chunker** at `apps/ingestion/chunker.py` — wraps `langchain_text_splitters.RecursiveCharacterTextSplitter` with per-source-type sizing. One public function: `chunk_item(content, *, source_type, item_index) → list[Chunk]`. Enforces `MIN_CHUNK_CHARS=50` and `MAX_CHUNK_TOKENS=600`.
- **Payload builder** at `apps/ingestion/payload.py` — builds the 20-field Qdrant payload dict per chunk. NamedTuples / dataclasses for the `ScrapedSource` and `ScrapedItem` shapes Phase 5 will produce.
- **`pyproject.toml` extension** — adds `FlagEmbedding>=1.3` and `torch>=2.4` (CPU-only via the existing `pytorch-cpu` index) to production deps. Plus `transformers` (transitive, needed for the tokenizer).
- **`scripts/verify_setup.py` extension** — `--full` mode now also loads BGE-M3 and verifies all three vector types come back at correct dims. Phase 1's default behavior + Phase 3's collection round-trip are preserved exactly.
- **Tests**:
  - `tests/test_chunker.py` — pure unit tests, mocks `count_tokens`. Fast (<1s).
  - `tests/test_payload.py` — pure unit tests, no embedder load. Fast (<1s).
  - `tests/test_embedder.py` — integration tests that load real BGE-M3 once via session fixture. Marked `@pytest.mark.embedder` so it can be skipped with `pytest -m "not embedder"`.

After Phase 4: given an item.content string + `(tenant_id, bot_id, doc_id, source_type, ...)`, Phase 5 can call `chunker.chunk_item()` → `embedder.embed_passages()` → `payload.build_payload()` and end up with `(chunk_id, vectors_dict, payload_dict)` tuples ready to upsert via Phase 3's `qdrant_core.collection`. **Still no upload API endpoint, no orchestration, no Qdrant writes from Phase 4 itself.**

---

## Read first

- `build_prompts/phase_3_qdrant_layer/spec.md` — locked Qdrant collection schema (dense 1024 cosine + sparse `bm25` IDF + ColBERT 1024-per-token max_sim m=0)
- `build_prompts/phase_3_qdrant_layer/implementation_report.md` — confirms Phase 3 deliverables and the host-side `QDRANT_HOST=localhost` test override
- `build_prompts/phase_2_domain_models/spec.md` — `chunk_id` format `{doc_id}__i{item_index}__c{chunk_index}` is locked
- `build_prompts/phase_1_foundation/spec.md` — locked stack, container layout, `pytorch-cpu` index already configured
- `README.md` — project overview
- `rag_system_guide.md` (if present) — §3 "How Qdrant Fits In" for the embedder's role; §3 Step 4 has the BGE-M3 vector-type explanation

---

## Hard constraints (read before writing any code)

1. **Phase 1, 2, and 3 are locked.** Do NOT modify their deliverables EXCEPT:
   - `pyproject.toml` is extended (add deps + `[tool.uv.sources]` mapping for torch).
   - `scripts/verify_setup.py` is extended (add a third optional check inside `--full`).
   - `uv.lock` is regenerated (commit it).
   - That's it.

2. **Embedding library: FlagEmbedding (NOT FastEmbed).** Use `BGEM3FlagModel`. fp16 always. The decision is locked from Phase 1 and was extensively debated — do not switch to FastEmbed even though the dependency footprint is bigger.

3. **Torch is CPU-only.** Add `[tool.uv.sources]` mapping `torch = { index = "pytorch-cpu" }` to `pyproject.toml`. Without this, uv pulls the CUDA wheel by default and the image bloats from ~700 MB to ~3.5 GB. **Verify after build:** `docker compose exec web uv pip list | grep torch` shows `+cpu` in the version string (e.g., `torch  2.4.x+cpu`).

4. **No CUDA, no GPU, anywhere.** `BGE_DEVICE=cpu` from `.env`. Never default to `"cuda"` or `"auto"`.

5. **One-pass embed produces all three vectors.** `model.encode(..., return_dense=True, return_sparse=True, return_colbert_vecs=True)`. Three forward passes is wrong — that's FastEmbed's pattern, not FlagEmbedding's.

6. **Per-source-type chunk config is locked:**
   - pdf, docx → 500 tokens, 15% overlap
   - url, html → 400 tokens, 10% overlap
   - csv, faq → 200 tokens, 10% overlap
   - image → 300 tokens, 10% overlap
   - default (unknown source_type) → 400 tokens, 10% overlap
   - `MIN_CHUNK_CHARS = 50` (drop or merge below this)
   - `MAX_CHUNK_TOKENS = 600` (hard truncate)

7. **Chunks NEVER cross item boundaries.** `chunk_item()` takes ONE item's content and returns chunks fully contained within it. Phase 5 iterates `items[]` and concatenates results.

8. **`chunk_id` format** (from Phase 2): `f"{doc_id}__i{item_index}__c{chunk_index}"`. Always. The grep guard test from Phase 2 still applies — only `apps/ingestion/payload.py` and tests/fixtures should construct strings matching `__i\d+__c\d+`.

9. **All payloads ALWAYS write `version=1` and `is_active=True`.** Atomic version swap is v2; for v1 every chunk is "the live version".

10. **Tokenizer = embedder's tokenizer.** Use BGE-M3's XLM-RoBERTa tokenizer for `count_tokens()`. Don't approximate via `len(text)//4` in production (only in tests where speed matters and exact counts don't).

11. **Lazy module-level singleton for the model.** Same pattern as Phase 3's `QdrantClient`. `@functools.lru_cache(maxsize=1)`. POST-fork construction; never load at module import time.

12. **No HTTP/gRPC endpoints, no orchestration.** Phase 4 ships pure Python modules. Phase 5 wires them into the upload pipeline.

13. **No code comments unless the spec or a non-obvious invariant justifies them.**

---

## Stack & versions (extends Phase 1)

New entries in `pyproject.toml`:

| Package | Constraint | Notes |
|---|---|---|
| `FlagEmbedding` | `>=1.3` | BAAI's official BGE-M3 lib |
| `torch` | `>=2.4` | **CPU-only** via `[tool.uv.sources]` |

Transitive deps that arrive automatically: `transformers`, `tokenizers`, `huggingface-hub`, `safetensors`, `numpy`, etc. The lockfile regeneration will pick them up.

`pyproject.toml` MUST contain:

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
```

The `[[tool.uv.index]]` block already exists from Phase 1 — verify it's still there and add the `[tool.uv.sources]` mapping if absent.

---

## Deliverables

```
qdrant_rag/
├── apps/ingestion/
│   ├── embedder.py                ← NEW
│   ├── chunker.py                 ← NEW
│   ├── payload.py                 ← NEW
│   ├── apps.py                    ← UNCHANGED stub
│   └── __init__.py                ← UNCHANGED stub
├── pyproject.toml                 ← EXTEND (deps + sources)
├── uv.lock                        ← REGENERATED (commit)
├── scripts/verify_setup.py        ← EXTEND (--full now also exercises embedder)
└── tests/
    ├── test_embedder.py           ← NEW (integration; @pytest.mark.embedder)
    ├── test_chunker.py            ← NEW (unit)
    └── test_payload.py            ← NEW (unit)
```

3 new source files + 3 new test files + 2 modified files (pyproject.toml, verify_setup.py) + 1 regenerated lockfile.

---

## File-by-file specification

### `pyproject.toml` (EXTEND)

Add to `[project].dependencies`:

```
"FlagEmbedding>=1.3",
"torch>=2.4",
```

Below the existing `[[tool.uv.index]]` for `pytorch-cpu`, add:

```toml
[tool.uv.sources]
torch = { index = "pytorch-cpu" }
```

Run `uv sync` to regenerate `uv.lock`. **Verify the lock file shows torch with a `+cpu` build suffix.**

Add a pytest marker registration in the existing `[tool.pytest.ini_options]` block:

```toml
markers = [
    "embedder: tests that require the BGE-M3 model to be loaded (slow, ~30s+ first run)",
]
```

### `apps/ingestion/embedder.py` (NEW)

```python
"""BGE-M3 embedder via FlagEmbedding. CPU-only. fp16.

Lazy module-level singleton: model loads on first call, not at import.
Each gunicorn worker / Celery worker / management command process loads
its own copy (POST-fork). Each load takes ~30-60s on CPU; ~1.8 GB RAM
per process for the fp16 weights.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

DENSE_DIM = 1024
COLBERT_DIM = 1024


@functools.lru_cache(maxsize=1)
def _get_model() -> Any:
    from FlagEmbedding import BGEM3FlagModel

    cfg = settings.BGE
    started = time.monotonic()
    logger.info(
        "bge_m3_loading",
        extra={
            "model": cfg["MODEL_NAME"],
            "device": cfg["DEVICE"],
            "use_fp16": cfg["USE_FP16"],
            "cache_dir": cfg["CACHE_DIR"],
        },
    )
    model = BGEM3FlagModel(
        cfg["MODEL_NAME"],
        use_fp16=cfg["USE_FP16"],
        cache_dir=cfg["CACHE_DIR"],
        devices=[cfg["DEVICE"]],  # FlagEmbedding 1.4.0+ takes a list, not a single string
    )
    logger.info(
        "bge_m3_loaded",
        extra={"elapsed_s": round(time.monotonic() - started, 2)},
    )
    return model


@functools.lru_cache(maxsize=1)
def _get_tokenizer() -> Any:
    from transformers import AutoTokenizer

    cfg = settings.BGE
    return AutoTokenizer.from_pretrained(
        cfg["MODEL_NAME"],
        cache_dir=cfg["CACHE_DIR"],
    )


def count_tokens(text: str) -> int:
    """Token count via BGE-M3's tokenizer (XLM-RoBERTa).

    Used by the chunker to enforce MAX_CHUNK_TOKENS. Same tokenizer the
    embedder uses, so the chunker's view matches the model's view.
    """
    if not text:
        return 0
    tokenizer = _get_tokenizer()
    return len(tokenizer.encode(text, add_special_tokens=False))


def embed_passages(texts: list[str]) -> dict[str, Any]:
    """Embed multiple passages in one batch.

    Returns:
        {
            "dense":   list[ndarray (1024,)],         length = len(texts)
            "sparse":  list[dict[str, float]],        length = len(texts)
            "colbert": list[ndarray (n_tokens, 1024)] length = len(texts)
        }

    Raises:
        ValueError if `texts` is empty or any element is empty/whitespace.
    """
    if not texts:
        raise ValueError("embed_passages requires at least one input")
    for i, t in enumerate(texts):
        if not isinstance(t, str) or not t.strip():
            raise ValueError(f"texts[{i}] is empty or not a string")

    model = _get_model()
    cfg = settings.BGE
    started = time.monotonic()
    out = model.encode(
        texts,
        batch_size=cfg["BATCH_SIZE"],
        max_length=8192,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=True,
    )
    logger.debug(
        "bge_m3_embed_passages",
        extra={
            "n_inputs": len(texts),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return {
        "dense": out["dense_vecs"],
        "sparse": out["lexical_weights"],
        "colbert": out["colbert_vecs"],
    }


def embed_query(text: str) -> dict[str, Any]:
    """Single-query convenience wrapper.

    Returns:
        {
            "dense":   ndarray (1024,),
            "sparse":  dict[str, float],
            "colbert": ndarray (n_tokens, 1024),
        }
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("embed_query requires a non-empty string")
    out = embed_passages([text])
    return {
        "dense": out["dense"][0],
        "sparse": out["sparse"][0],
        "colbert": out["colbert"][0],
    }


def sparse_to_qdrant(sparse_dict: dict[str, float]) -> dict[str, list]:
    """Convert FlagEmbedding's sparse output to Qdrant SparseVector format.

    FlagEmbedding emits {token_id_str: weight, ...}.
    Qdrant SparseVector expects {indices: [int], values: [float]}.

    Returns a dict; caller wraps in qdrant_client.models.SparseVector(**result).
    """
    if not sparse_dict:
        return {"indices": [], "values": []}
    indices: list[int] = []
    values: list[float] = []
    for token_id, weight in sparse_dict.items():
        indices.append(int(token_id))
        values.append(float(weight))
    return {"indices": indices, "values": values}


def colbert_to_qdrant(colbert_vec: Any) -> list[list[float]]:
    """Convert FlagEmbedding's ColBERT output to Qdrant multivector format.

    FlagEmbedding emits ndarray of shape (n_tokens, 1024).
    Qdrant multivector expects list[list[float]] of the same shape.
    """
    import numpy as np

    if isinstance(colbert_vec, np.ndarray):
        return colbert_vec.tolist()
    return [list(row) for row in colbert_vec]


def warmup() -> None:
    """Force model + tokenizer load. Use from verify_setup.py or a CLI."""
    _get_model()
    _get_tokenizer()
```

### `apps/ingestion/chunker.py` (NEW)

```python
"""Per-source-type chunker.

Wraps langchain-text-splitters' RecursiveCharacterTextSplitter with
sizing locked per source type. Tokens counted via BGE-M3's tokenizer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from apps.ingestion.embedder import count_tokens

logger = logging.getLogger(__name__)


CHUNK_CONFIG: dict[str, dict[str, float]] = {
    "pdf":   {"size": 500, "overlap_pct": 0.15},
    "docx":  {"size": 500, "overlap_pct": 0.15},
    "url":   {"size": 400, "overlap_pct": 0.10},
    "html":  {"size": 400, "overlap_pct": 0.10},
    "csv":   {"size": 200, "overlap_pct": 0.10},
    "faq":   {"size": 200, "overlap_pct": 0.10},
    "image": {"size": 300, "overlap_pct": 0.10},
}
DEFAULT_CHUNK_CONFIG: dict[str, float] = {"size": 400, "overlap_pct": 0.10}
MIN_CHUNK_CHARS = 50
MAX_CHUNK_TOKENS = 600

# Conservative chars-per-token estimate. Splitter operates on characters;
# we then verify with actual token counts and hard-truncate if needed.
_CHARS_PER_TOKEN = 4

_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", ", ", " ", ""]


@dataclass(frozen=True)
class Chunk:
    text: str
    chunk_index: int
    char_count: int
    token_count: int


def chunk_item(
    content: str,
    *,
    source_type: str,
    item_index: int,  # passed for log context only; chunks are renumbered locally
) -> list[Chunk]:
    """Split one item.content into chunks per the locked per-source-type config.

    Returns chunks fully contained within `content` (never crosses item
    boundaries). Empty/whitespace-only `content` returns []. Very short
    content (less than MIN_CHUNK_CHARS) returns a single chunk = the
    content itself, NOT [].

    `chunk_index` on each Chunk starts at 0 within this item; the caller
    composes the global chunk_id via `payload.build_chunk_id`.
    """
    if not content or not content.strip():
        return []

    config = CHUNK_CONFIG.get(source_type, DEFAULT_CHUNK_CONFIG)
    if source_type not in CHUNK_CONFIG:
        logger.warning(
            "chunker_unknown_source_type",
            extra={"source_type": source_type, "fallback": "default"},
        )

    target_tokens = int(config["size"])
    overlap_tokens = int(target_tokens * config["overlap_pct"])

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=target_tokens * _CHARS_PER_TOKEN,
        chunk_overlap=overlap_tokens * _CHARS_PER_TOKEN,
        length_function=len,
        separators=_SEPARATORS,
    )
    raw_chunks = [c.strip() for c in splitter.split_text(content) if c.strip()]

    chunks: list[Chunk] = []
    for raw_text in raw_chunks:
        text = _truncate_to_max_tokens(raw_text)
        if not text:
            continue

        token_n = count_tokens(text)

        # Merge tiny final fragment into prev if it fits
        if len(text) < MIN_CHUNK_CHARS and chunks:
            prev = chunks[-1]
            merged = f"{prev.text} {text}"
            merged_token_n = count_tokens(merged)
            if merged_token_n <= MAX_CHUNK_TOKENS:
                chunks[-1] = Chunk(
                    text=merged,
                    chunk_index=prev.chunk_index,
                    char_count=len(merged),
                    token_count=merged_token_n,
                )
                continue

        chunks.append(
            Chunk(
                text=text,
                chunk_index=len(chunks),
                char_count=len(text),
                token_count=token_n,
            )
        )

    if not chunks and content.strip():
        # Splitter dropped everything (very rare). Fall back to a single
        # whole-content chunk.
        text = _truncate_to_max_tokens(content.strip())
        chunks.append(
            Chunk(
                text=text,
                chunk_index=0,
                char_count=len(text),
                token_count=count_tokens(text),
            )
        )

    logger.debug(
        "chunker_done",
        extra={
            "source_type": source_type,
            "item_index": item_index,
            "input_chars": len(content),
            "n_chunks": len(chunks),
        },
    )
    return chunks


def _truncate_to_max_tokens(text: str) -> str:
    """Hard-cap text at MAX_CHUNK_TOKENS via a binary char-count search."""
    if not text:
        return text
    n = count_tokens(text)
    if n <= MAX_CHUNK_TOKENS:
        return text
    # Estimate target char count, then trim and re-verify.
    target_chars = int(len(text) * MAX_CHUNK_TOKENS / n)
    truncated = text[:target_chars].rstrip()
    # Token counts can fluctuate; if still over budget, trim further.
    while count_tokens(truncated) > MAX_CHUNK_TOKENS and truncated:
        truncated = truncated[: int(len(truncated) * 0.95)].rstrip()
    return truncated
```

### `apps/ingestion/payload.py` (NEW)

```python
"""Qdrant payload builder for chunks.

Produces the locked 20-field payload dict per chunk. Phase 5's pipeline
calls build_payload() per chunk after embedding; the result goes into
qdrant_client.models.PointStruct(payload=...).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from apps.ingestion.chunker import Chunk


@dataclass(frozen=True)
class ScrapedSource:
    type: str
    filename: str | None = None
    url: str | None = None
    content_hash: str = ""
    language: str | None = None


@dataclass(frozen=True)
class ScrapedItem:
    item_index: int
    item_type: str | None = None
    title: str | None = None
    section_path: list[str] = field(default_factory=list)
    page_number: int | None = None
    url: str | None = None
    language: str | None = None


def build_chunk_id(doc_id: str, item_index: int, chunk_index: int) -> str:
    return f"{doc_id}__i{item_index}__c{chunk_index}"


def build_payload(
    chunk: Chunk,
    *,
    tenant_id: str,
    bot_id: str,
    doc_id: str,
    item: ScrapedItem,
    source: ScrapedSource,
    custom_metadata: dict[str, Any] | None = None,
    uploaded_at: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Build the 20-field Qdrant payload for one chunk.

    Required fields are always populated. Optional fields default to None
    or empty-list/empty-string so the payload schema stays consistent
    across chunks.
    """
    custom = custom_metadata or {}
    now = uploaded_at or datetime.datetime.now(datetime.UTC)

    return {
        # Identity
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "doc_id": doc_id,
        "chunk_id": build_chunk_id(doc_id, item.item_index, chunk.chunk_index),
        # Lifecycle (locked: all v1 chunks are version=1, is_active=True)
        "version": 1,
        "is_active": True,
        "uploaded_at": now.isoformat(),
        # Source provenance
        "source_type": source.type,
        "source_filename": source.filename,
        "source_url": source.url or item.url,
        "source_item_index": item.item_index,
        "source_content_hash": source.content_hash,
        "section_title": item.title,
        "section_path": list(item.section_path),
        "page_number": item.page_number,
        # Content
        "text": chunk.text,
        "char_count": chunk.char_count,
        "token_count": chunk.token_count,
        # Tags
        "category": custom.get("category"),
        "tags": list(custom.get("tags", [])),
    }
```

### `scripts/verify_setup.py` (EXTEND)

Phase 1 + Phase 3 behavior preserved verbatim. Add a third optional check:

```python
# (after the existing roundtrip_qdrant_collection function from Phase 3)


def warmup_embedder() -> None:
    """Load BGE-M3 and verify all three vector types come back at correct dims."""
    print("[verify_setup --full] Loading BGE-M3 (this may take ~30-60s) ...")
    from apps.ingestion.embedder import (
        COLBERT_DIM,
        DENSE_DIM,
        embed_passages,
    )

    out = embed_passages(["A short sentence to verify embeddings produce all three vectors."])
    dense = out["dense"][0]
    sparse = out["sparse"][0]
    colbert = out["colbert"][0]

    if len(dense) != DENSE_DIM:
        raise SystemExit(
            f"[verify_setup --full] Dense dim mismatch: got {len(dense)}, want {DENSE_DIM}"
        )
    if not isinstance(sparse, dict) or not sparse:
        raise SystemExit(
            f"[verify_setup --full] Sparse must be a non-empty dict, got {type(sparse).__name__}"
        )
    # ColBERT is a 2D ndarray (tokens, COLBERT_DIM). Verify the second axis.
    if hasattr(colbert, "shape"):
        if colbert.shape[1] != COLBERT_DIM:
            raise SystemExit(
                f"[verify_setup --full] ColBERT inner dim: got {colbert.shape[1]}, want {COLBERT_DIM}"
            )
    else:
        # list-of-lists fallback
        if not colbert or len(colbert[0]) != COLBERT_DIM:
            raise SystemExit("[verify_setup --full] ColBERT inner dim mismatch")

    print(
        f"[verify_setup --full] Embedder OK. dense={len(dense)} "
        f"sparse_keys={len(sparse)} colbert_tokens={len(colbert)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify qdrant_rag setup.")
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "Also run a Qdrant collection round-trip AND load BGE-M3 to "
            "verify all three vector types (slow, ~30-60s)."
        ),
    )
    args = parser.parse_args()

    ping_postgres()
    ping_qdrant()
    if args.full:
        roundtrip_qdrant_collection()
        warmup_embedder()
    print("[verify_setup] All checks passed.")
```

### `tests/test_chunker.py` (NEW)

Pure unit tests. Mocks `count_tokens` so the embedder doesn't load.

```python
from unittest.mock import patch

import pytest

from apps.ingestion.chunker import (
    CHUNK_CONFIG,
    DEFAULT_CHUNK_CONFIG,
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_CHARS,
    Chunk,
    chunk_item,
)


def _fake_token_count(text: str) -> int:
    """Approximate: 1 token per 4 characters. Good enough for chunker logic."""
    return max(1, len(text) // 4)


@pytest.fixture(autouse=True)
def mock_count_tokens():
    with patch("apps.ingestion.chunker.count_tokens", side_effect=_fake_token_count):
        yield


class TestChunkItemBasic:
    def test_empty_content_returns_empty_list(self):
        assert chunk_item("", source_type="pdf", item_index=0) == []

    def test_whitespace_only_returns_empty_list(self):
        assert chunk_item("   \n\t  ", source_type="pdf", item_index=0) == []

    def test_short_content_returns_single_chunk(self):
        text = "Tiny."
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        assert len(chunks) == 1
        assert chunks[0].text == "Tiny."
        assert chunks[0].chunk_index == 0

    def test_chunk_indices_are_sequential(self):
        text = "Sentence one. " * 500
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        assert len(chunks) > 1
        for i, c in enumerate(chunks):
            assert c.chunk_index == i


class TestSourceTypeRouting:
    @pytest.mark.parametrize("source_type", list(CHUNK_CONFIG.keys()))
    def test_known_source_types_use_their_config(self, source_type):
        text = "x " * 5000
        chunks = chunk_item(text, source_type=source_type, item_index=0)
        assert len(chunks) > 0

    def test_unknown_source_type_uses_default(self):
        text = "x " * 5000
        chunks = chunk_item(text, source_type="unknown_type", item_index=0)
        assert len(chunks) > 0


class TestSizeLimits:
    def test_no_chunk_exceeds_max_tokens(self):
        text = ("Long sentence. " * 2000).strip()
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        for c in chunks:
            assert c.token_count <= MAX_CHUNK_TOKENS

    def test_tiny_final_chunk_is_merged(self):
        # Construct content where the splitter would emit a tiny tail.
        text = "Big chunk content. " * 100 + " End."
        chunks = chunk_item(text, source_type="pdf", item_index=0)
        # The final " End." would be < MIN_CHUNK_CHARS; verify it didn't
        # survive as its own chunk OR was merged in.
        for c in chunks[:-1]:
            assert c.char_count >= MIN_CHUNK_CHARS or c is chunks[0]
```

### `tests/test_payload.py` (NEW)

Pure unit tests. No embedder load.

```python
import datetime

import pytest

from apps.ingestion.chunker import Chunk
from apps.ingestion.payload import (
    ScrapedItem,
    ScrapedSource,
    build_chunk_id,
    build_payload,
)


def test_build_chunk_id_format():
    assert build_chunk_id("doc-abc", 0, 0) == "doc-abc__i0__c0"
    assert build_chunk_id("doc-xyz", 3, 12) == "doc-xyz__i3__c12"


def _make_chunk(text: str = "hello world") -> Chunk:
    return Chunk(text=text, chunk_index=0, char_count=len(text), token_count=2)


def _make_source(**overrides) -> ScrapedSource:
    return ScrapedSource(
        type=overrides.get("type", "pdf"),
        filename=overrides.get("filename", "doc.pdf"),
        url=overrides.get("url"),
        content_hash=overrides.get("content_hash", "sha256:abc"),
        language=overrides.get("language", "en"),
    )


def _make_item(**overrides) -> ScrapedItem:
    return ScrapedItem(
        item_index=overrides.get("item_index", 0),
        item_type=overrides.get("item_type", "page"),
        title=overrides.get("title", "Section A"),
        section_path=overrides.get("section_path", ["Top", "Section A"]),
        page_number=overrides.get("page_number", 1),
        url=overrides.get("url"),
        language=overrides.get("language", "en"),
    )


class TestBuildPayload:
    def test_required_fields_present(self):
        chunk = _make_chunk()
        p = build_payload(
            chunk,
            tenant_id="pizzapalace",
            bot_id="supportv1",
            doc_id="doc-abc",
            item=_make_item(),
            source=_make_source(),
        )
        assert p["tenant_id"] == "pizzapalace"
        assert p["bot_id"] == "supportv1"
        assert p["doc_id"] == "doc-abc"
        assert p["chunk_id"] == "doc-abc__i0__c0"
        assert p["version"] == 1
        assert p["is_active"] is True
        assert p["text"] == "hello world"
        assert p["char_count"] == 11
        assert p["token_count"] == 2

    def test_uploaded_at_is_iso8601(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(),
        )
        # ISO8601 parses cleanly:
        datetime.datetime.fromisoformat(p["uploaded_at"])

    def test_source_url_falls_back_to_item_url(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(url="https://example.com/page"),
            source=_make_source(url=None),
        )
        assert p["source_url"] == "https://example.com/page"

    def test_tags_default_to_empty_list(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(),
        )
        assert p["tags"] == []

    def test_tags_passed_through_from_custom_metadata(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(),
            source=_make_source(),
            custom_metadata={"category": "policy", "tags": ["refund", "delivery"]},
        )
        assert p["category"] == "policy"
        assert p["tags"] == ["refund", "delivery"]

    def test_section_path_is_list_copy(self):
        path = ["A", "B", "C"]
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="d",
            item=_make_item(section_path=path),
            source=_make_source(),
        )
        assert p["section_path"] == path
        # Confirm a copy was made (mutating the input doesn't affect output)
        path.append("D")
        assert p["section_path"] == ["A", "B", "C"]

    def test_chunk_id_from_payload_matches_helper(self):
        p = build_payload(
            _make_chunk(),
            tenant_id="t",
            bot_id="b",
            doc_id="myDocId",
            item=_make_item(item_index=4),
            source=_make_source(),
        )
        assert p["chunk_id"] == build_chunk_id("myDocId", 4, 0)
```

### `tests/test_embedder.py` (NEW)

Integration tests. Loads the real model. Marked `@pytest.mark.embedder`. Skip-not-fail if FlagEmbedding can't load.

```python
import numpy as np
import pytest

pytestmark = pytest.mark.embedder


@pytest.fixture(scope="session", autouse=True)
def model_loadable():
    """Load the model once. Skip the whole module if it can't load
    (e.g., no network, no disk space, no Python deps)."""
    try:
        from apps.ingestion.embedder import _get_model

        _get_model()
    except Exception as exc:
        pytest.skip(f"BGE-M3 cannot load in this environment: {exc}")


class TestCountTokens:
    def test_empty_returns_zero(self):
        from apps.ingestion.embedder import count_tokens

        assert count_tokens("") == 0

    def test_short_text(self):
        from apps.ingestion.embedder import count_tokens

        n = count_tokens("Hello world.")
        assert 1 < n < 10  # tokenizer-dependent, but should be in this range


class TestEmbedPassages:
    def test_returns_three_vector_types(self):
        from apps.ingestion.embedder import (
            COLBERT_DIM,
            DENSE_DIM,
            embed_passages,
        )

        out = embed_passages(["First sentence.", "Second sentence."])

        # Dense
        assert len(out["dense"]) == 2
        assert len(out["dense"][0]) == DENSE_DIM
        # Sparse
        assert len(out["sparse"]) == 2
        assert isinstance(out["sparse"][0], dict)
        assert len(out["sparse"][0]) > 0
        # ColBERT
        assert len(out["colbert"]) == 2
        # Each colbert entry is (n_tokens, COLBERT_DIM)
        for cv in out["colbert"]:
            assert cv.shape[1] == COLBERT_DIM

    def test_empty_input_raises(self):
        from apps.ingestion.embedder import embed_passages

        with pytest.raises(ValueError):
            embed_passages([])

    def test_whitespace_only_input_raises(self):
        from apps.ingestion.embedder import embed_passages

        with pytest.raises(ValueError):
            embed_passages(["   \n  "])

    def test_deterministic_within_tolerance(self):
        from apps.ingestion.embedder import embed_passages

        a = embed_passages(["test sentence"])
        b = embed_passages(["test sentence"])
        # fp16 has tiny nondeterminism; use np.allclose with rtol=1e-2
        assert np.allclose(a["dense"][0], b["dense"][0], rtol=1e-2, atol=1e-3)


class TestEmbedQuery:
    def test_returns_single_set(self):
        from apps.ingestion.embedder import (
            COLBERT_DIM,
            DENSE_DIM,
            embed_query,
        )

        out = embed_query("a refund question")
        assert len(out["dense"]) == DENSE_DIM
        assert isinstance(out["sparse"], dict)
        assert out["colbert"].shape[1] == COLBERT_DIM


class TestSparseToQdrant:
    def test_converts_keys_to_int_indices(self):
        from apps.ingestion.embedder import sparse_to_qdrant

        result = sparse_to_qdrant({"42": 0.9, "100": 0.1})
        assert sorted(result["indices"]) == [42, 100]
        assert all(isinstance(v, float) for v in result["values"])

    def test_empty_input(self):
        from apps.ingestion.embedder import sparse_to_qdrant

        assert sparse_to_qdrant({}) == {"indices": [], "values": []}


class TestColbertToQdrant:
    def test_converts_ndarray_to_list_of_lists(self):
        from apps.ingestion.embedder import colbert_to_qdrant

        arr = np.zeros((3, 1024), dtype=np.float32)
        result = colbert_to_qdrant(arr)
        assert isinstance(result, list)
        assert len(result) == 3
        assert len(result[0]) == 1024
        assert isinstance(result[0][0], float)
```

---

## Acceptance criteria

Phase 4 is complete when **all** of these pass:

1. `uv sync` regenerates `uv.lock` with `torch` resolving to a `+cpu` build. Verify in `uv.lock` (search for `+cpu` or `download.pytorch.org/whl/cpu`).
2. `uv run ruff check .` reports zero violations.
3. `uv run ruff format --check .` passes.
4. Fast test subset: `uv run pytest tests/test_chunker.py tests/test_payload.py -v` is green and runs in under 5 seconds.
5. Embedder tests from host (slow, requires network on first run): `uv run pytest -m embedder -v` either passes or skips with a clear "BGE-M3 cannot load" message.
6. `docker compose -f docker-compose.yml up -d --build` brings the stack up green. Image rebuild may take 5–10 minutes (torch + transformers download).
7. `docker compose -f docker-compose.yml exec web uv pip list | grep -i torch` shows torch with `+cpu` suffix and NO CUDA-related packages (`nvidia-*`, `cuda-*`).
8. `docker compose -f docker-compose.yml exec web python scripts/verify_setup.py --full` exits 0 (loads model + runs Qdrant round-trip + warmup_embedder check). First run takes ~60-90s on CPU.
9. Embedder tests in container: `docker compose -f docker-compose.yml exec web pytest -m embedder -v` is green. Subsequent runs are faster because the model is cached in `bge_cache` volume.
10. Full suite from host: `uv run pytest -v` keeps Phase 1's healthz, Phase 2's models, and Phase 3's qdrant tests green alongside Phase 4. Embedder tests skip gracefully if model can't load on host. `curl -fsS http://localhost:8080/healthz | python -m json.tool` still returns the green JSON.

---

## Common pitfalls

1. **torch resolves to CUDA wheel.** If `[tool.uv.sources]` is missing OR the `[[tool.uv.index]] explicit = true` is removed, uv uses the default index. The CUDA wheel is ~2 GB and the image bloats. Verify by inspecting `uv.lock` or `docker compose exec web uv pip list | grep -i nvidia` (must be empty).

2. **FastEmbed sneaks in via qdrant-client extras.** Phase 1 explicitly used plain `qdrant-client` without `[fastembed]`. If a future dep change adds `qdrant-client[fastembed]`, FastEmbed loads alongside FlagEmbedding, doubling memory. Verify Phase 1's import unchanged.

3. **Model loads at module import time.** If `_get_model()` is called outside a function (module-level), gunicorn's master loads the model before forking. Workers inherit a corrupted state. The `lru_cache` decorator on the function is the safety: only call `_get_model()` from inside another function.

4. **Tokenizer mismatch between chunker and embedder.** The chunker counts tokens to enforce MAX_CHUNK_TOKENS. If it uses a different tokenizer than the embedder, chunks may exceed the model's max_length on actual encode. Use `apps.ingestion.embedder.count_tokens` (which uses BGE-M3's tokenizer) — never approximate.

5. **Sparse format conversion error.** FlagEmbedding's sparse output uses STRING token IDs (`{"42": 0.9}`). Qdrant SparseVector expects INT indices. Convert via `int(token_id_str)`. If you forget, Qdrant raises a type error on upsert.

6. **ColBERT shape error.** FlagEmbedding emits a 2D ndarray of shape `(n_tokens, 1024)` per chunk. Qdrant multivector expects `list[list[float]]` of the same shape. Use `colbert_to_qdrant()` — don't pass the ndarray directly.

7. **`MIN_CHUNK_CHARS` drops the only chunk for short content.** If `content` is short (e.g., 30 chars), naive splitting + filter would return zero chunks. The chunker handles this by always returning at least one chunk if `content.strip()` is non-empty — the fallback at the end of `chunk_item`.

8. **`MAX_CHUNK_TOKENS` not enforced after splitter.** The langchain splitter operates on character counts, not tokens. A chunk that's `target_tokens * _CHARS_PER_TOKEN` characters might exceed `MAX_CHUNK_TOKENS` because some text has more tokens-per-character (e.g., languages with smaller subword units). Always re-verify with `count_tokens()` and truncate via `_truncate_to_max_tokens()`.

9. **Embedder tests run in CI without bge_cache.** First-run model download requires HuggingFace network access AND ~1.14 GB disk. CI must either (a) cache the volume or (b) skip embedder tests by default with `-m "not embedder"`. The session fixture skip-not-fail handles this gracefully.

10. **Model loads twice when running tests + verify_setup.** The `lru_cache` is per-process, not per-test-session. If a test runs `verify_setup.py` as a subprocess, the subprocess loads its own model copy. Avoid: tests should call `embed_passages()` directly, not invoke `verify_setup.py` from within tests.

---

## Out of scope for Phase 4 (explicit)

Do NOT implement these in Phase 4:

- DRF serializers for upload — Phase 5
- POST `/v1/.../documents` endpoint — Phase 5
- Pipeline orchestrator (validate → lock → chunk → embed → upsert) — Phase 5
- Postgres advisory lock acquisition — Phase 5 (helper exists from Phase 2)
- DELETE endpoint — Phase 6
- gRPC search service / `search.proto` — Phase 7
- Hybrid search query (RRF + ColBERT rerank) — Phase 7
- Quantization — v4
- Atomic version swap — v2
- Audit log — v3
- BGE-M3 fine-tuning — never (use stock model)
- Async embedding via Celery — v2

If you find yourself writing any of the above, stop.

---

## When you finish

1. Confirm all 10 acceptance criteria pass.
2. Commit:
   - `apps/ingestion/{embedder,chunker,payload}.py`
   - `tests/test_{embedder,chunker,payload}.py`
   - `pyproject.toml`, `uv.lock`
   - `scripts/verify_setup.py` (extension)
3. Verify NO Phase 1/2/3 source file changed (other than the explicitly-authorized scripts/verify_setup.py extension and pyproject.toml).
4. Output a short report with file counts, criterion ✓/✗, deviations, defects.

That's Phase 4. Phase 5 (Upload API) wires chunker → embedder → payload-builder → qdrant_core into the orchestrated upload pipeline.
