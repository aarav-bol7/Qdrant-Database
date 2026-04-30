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
        devices=cfg["DEVICE"],
    )
    logger.info(
        "bge_m3_loaded",
        extra={"elapsed_s": round(time.monotonic() - started, 2)},
    )
    try:
        from apps.core.metrics import embedder_loaded

        embedder_loaded.set(1)
    except Exception:
        logger.exception("embedder_loaded_metric_set_failed")
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
