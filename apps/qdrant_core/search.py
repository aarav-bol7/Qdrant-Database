"""Hybrid search query builder.

Encapsulates the locked retrieval algorithm: embed query -> single
client.query_points() with prefetch (50 dense x3 + 50 sparse, fused via
RRF) -> ColBERT rerank -> top-K.

Weighted RRF (3:1 dense:sparse) is emulated by duplicating the dense
prefetch RRF_DENSE_WEIGHT times. qdrant-client 1.17.1 exposes only
plain Fusion.RRF; with three identical dense input lists each candidate
contributes 3 / (k + rank_d) + 1 / (k + rank_s) -- exactly the spec.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    SparseVector,
)

from apps.ingestion.embedder import (
    colbert_to_qdrant,
    embed_query,
    sparse_to_qdrant,
)
from apps.qdrant_core.client import get_qdrant_client, with_retry
from apps.qdrant_core.collection import (
    COLBERT_VECTOR_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
)
from apps.qdrant_core.exceptions import QdrantOperationError
from apps.qdrant_core.naming import collection_name

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
MAX_TOP_K = 20
SCORE_THRESHOLD = 0.0
PREFETCH_LIMIT = 50
FUSION_LIMIT = 100
RRF_DENSE_WEIGHT = 3
RRF_SPARSE_WEIGHT = 1


class CollectionNotFoundError(QdrantOperationError):
    """Raised when a search targets a bot whose collection does not exist."""


def search(
    *,
    tenant_id: str,
    bot_id: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    source_types: list[str] | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()

    if not client.collection_exists(name):
        raise CollectionNotFoundError(f"Collection {name!r} does not exist.")

    embeddings = embed_query(query)
    dense_obj = embeddings["dense"]
    dense_vec = dense_obj.tolist() if hasattr(dense_obj, "tolist") else list(dense_obj)
    sparse_qd = sparse_to_qdrant(embeddings["sparse"])
    colbert_vec = colbert_to_qdrant(embeddings["colbert"])

    qfilter = _build_filter(source_types=source_types, tags=tags, category=category)
    inner_prefetches = _build_inner_prefetches(
        dense_vec=dense_vec, sparse_qd=sparse_qd, qfilter=qfilter
    )
    fusion_node = Prefetch(
        prefetch=inner_prefetches,
        query=FusionQuery(fusion=Fusion.RRF),
        limit=FUSION_LIMIT,
    )

    return _execute_query(
        client=client,
        collection=name,
        prefetch=[fusion_node],
        colbert_vec=colbert_vec,
        qfilter=qfilter,
        top_k=top_k,
    )


def _build_filter(
    *,
    source_types: list[str] | None,
    tags: list[str] | None,
    category: str | None,
) -> Filter:
    must: list[FieldCondition] = [
        FieldCondition(key="is_active", match=MatchValue(value=True)),
    ]
    if source_types:
        must.append(FieldCondition(key="source_type", match=MatchAny(any=source_types)))
    if tags:
        must.append(FieldCondition(key="tags", match=MatchAny(any=tags)))
    if category:
        must.append(FieldCondition(key="category", match=MatchValue(value=category)))
    return Filter(must=must)


def _build_inner_prefetches(
    *,
    dense_vec: list[float],
    sparse_qd: dict[str, list],
    qfilter: Filter,
) -> list[Prefetch]:
    sparse_vector = SparseVector(indices=sparse_qd["indices"], values=sparse_qd["values"])
    dense_prefetches = [
        Prefetch(
            query=dense_vec,
            using=DENSE_VECTOR_NAME,
            limit=PREFETCH_LIMIT,
            filter=qfilter,
        )
        for _ in range(RRF_DENSE_WEIGHT)
    ]
    sparse_prefetches = [
        Prefetch(
            query=sparse_vector,
            using=SPARSE_VECTOR_NAME,
            limit=PREFETCH_LIMIT,
            filter=qfilter,
        )
        for _ in range(RRF_SPARSE_WEIGHT)
    ]
    return dense_prefetches + sparse_prefetches


@with_retry()
def _execute_query(
    *,
    client: Any,
    collection: str,
    prefetch: list[Prefetch],
    colbert_vec: list[list[float]],
    qfilter: Filter,
    top_k: int,
) -> dict[str, Any]:
    response = client.query_points(
        collection_name=collection,
        prefetch=prefetch,
        query=colbert_vec,
        using=COLBERT_VECTOR_NAME,
        limit=top_k,
        score_threshold=SCORE_THRESHOLD,
        with_payload=True,
        with_vectors=False,
        query_filter=qfilter,
    )

    points = response.points
    chunks = []
    for p in points:
        payload = dict(p.payload or {})
        payload["score"] = float(p.score)
        chunks.append(payload)

    result = {
        "chunks": chunks,
        "total_candidates": len(points),
        "threshold_used": SCORE_THRESHOLD,
    }
    try:
        from apps.core.metrics import search_results_count, search_threshold_used

        search_results_count.observe(float(result["total_candidates"]))
        search_threshold_used.set(float(result["threshold_used"]))
    except Exception:
        logger.exception("search_metrics_record_failed")
    return result
