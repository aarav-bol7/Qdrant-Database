"""Per-bot Qdrant collection management.

The schema is locked: dense (1024 cosine HNSW m=16) + sparse bm25 (IDF) +
ColBERT (1024-per-token, max_sim, HNSW disabled). 8 payload indexes.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    KeywordIndexParams,
    MatchValue,
    Modifier,
    MultiVectorComparator,
    MultiVectorConfig,
    PayloadSchemaType,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from apps.qdrant_core.client import get_qdrant_client, with_retry
from apps.qdrant_core.exceptions import (
    CollectionSchemaMismatchError,
    QdrantOperationError,
)
from apps.qdrant_core.naming import collection_name

logger = logging.getLogger(__name__)


DENSE_VECTOR_NAME = "dense"
DENSE_VECTOR_SIZE = 1024
DENSE_HNSW_M = 16
DENSE_HNSW_EF_CONSTRUCT = 128

SPARSE_VECTOR_NAME = "bm25"

COLBERT_VECTOR_NAME = "colbert"
COLBERT_VECTOR_SIZE = 1024

assert COLBERT_VECTOR_SIZE == 1024, (
    "ColBERT vector size must be 1024 (BGE-M3), not 128 (vanilla ColBERTv2)"
)


PAYLOAD_INDEXES: list[tuple[str, Any]] = [
    ("doc_id", PayloadSchemaType.KEYWORD),
    ("source_type", PayloadSchemaType.KEYWORD),
    ("source_url", PayloadSchemaType.KEYWORD),
    ("language", PayloadSchemaType.KEYWORD),
    ("tags", PayloadSchemaType.KEYWORD),
    ("category", PayloadSchemaType.KEYWORD),
    ("is_active", PayloadSchemaType.BOOL),
    ("tenant_id", KeywordIndexParams(type="keyword", is_tenant=True)),
]


def _expected_vectors_config() -> dict[str, VectorParams]:
    return {
        DENSE_VECTOR_NAME: VectorParams(
            size=DENSE_VECTOR_SIZE,
            distance=Distance.COSINE,
            hnsw_config=HnswConfigDiff(
                m=DENSE_HNSW_M,
                ef_construct=DENSE_HNSW_EF_CONSTRUCT,
            ),
        ),
        COLBERT_VECTOR_NAME: VectorParams(
            size=COLBERT_VECTOR_SIZE,
            distance=Distance.COSINE,
            multivector_config=MultiVectorConfig(
                comparator=MultiVectorComparator.MAX_SIM,
            ),
            hnsw_config=HnswConfigDiff(m=0),
        ),
    }


def _expected_sparse_vectors_config() -> dict[str, SparseVectorParams]:
    return {
        SPARSE_VECTOR_NAME: SparseVectorParams(
            index=SparseIndexParams(on_disk=False),
            modifier=Modifier.IDF,
        ),
    }


@with_retry()
def create_collection_for_bot(tenant_id: str, bot_id: str) -> str:
    """Create the per-bot collection with the locked schema.

    Raises QdrantOperationError if the collection exists. Use
    get_or_create_collection() for the idempotent semantic.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()
    client.create_collection(
        collection_name=name,
        vectors_config=_expected_vectors_config(),
        sparse_vectors_config=_expected_sparse_vectors_config(),
    )
    for field_name, schema in PAYLOAD_INDEXES:
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=schema,
        )
    logger.info(
        "qdrant_collection_created",
        extra={"collection_name": name, "tenant_id": tenant_id, "bot_id": bot_id},
    )
    return name


@with_retry()
def get_or_create_collection(tenant_id: str, bot_id: str) -> str:
    """Idempotent: create if missing, verify schema if exists.

    Raises CollectionSchemaMismatchError if an existing collection's
    schema differs from expected.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()

    if client.collection_exists(name):
        diff = _compare_schema(client, name)
        if diff:
            raise CollectionSchemaMismatchError(name, diff)
        return name

    try:
        return create_collection_for_bot(tenant_id, bot_id)
    except UnexpectedResponse as exc:
        if getattr(exc, "status_code", None) == 409:
            diff = _compare_schema(client, name)
            if diff:
                raise CollectionSchemaMismatchError(name, diff) from exc
            return name
        raise QdrantOperationError(f"create_collection failed: {exc}") from exc


def _compare_schema(client: QdrantClient, name: str) -> dict[str, str]:
    """Return a diff dict describing how the actual schema differs from
    the expected one. Empty dict means schemas match.
    """
    info = client.get_collection(name)
    actual = info.config.params

    diff: dict[str, str] = {}

    actual_dense = actual.vectors.get(DENSE_VECTOR_NAME) if actual.vectors else None
    if not actual_dense or actual_dense.size != DENSE_VECTOR_SIZE:
        diff["dense.size"] = (
            f"expected {DENSE_VECTOR_SIZE}, got {getattr(actual_dense, 'size', 'MISSING')}"
        )
    if actual_dense and actual_dense.distance != Distance.COSINE:
        diff["dense.distance"] = f"expected COSINE, got {actual_dense.distance}"

    actual_colbert = actual.vectors.get(COLBERT_VECTOR_NAME) if actual.vectors else None
    if not actual_colbert or actual_colbert.size != COLBERT_VECTOR_SIZE:
        diff["colbert.size"] = (
            f"expected {COLBERT_VECTOR_SIZE}, got {getattr(actual_colbert, 'size', 'MISSING')}"
        )
    if actual_colbert and not actual_colbert.multivector_config:
        diff["colbert.multivector"] = "expected enabled, got disabled"

    actual_sparse = actual.sparse_vectors or {}
    if SPARSE_VECTOR_NAME not in actual_sparse:
        diff[f"sparse.{SPARSE_VECTOR_NAME}"] = "MISSING"
    else:
        sp = actual_sparse[SPARSE_VECTOR_NAME]
        if sp.modifier != Modifier.IDF:
            diff[f"sparse.{SPARSE_VECTOR_NAME}.modifier"] = f"expected IDF, got {sp.modifier}"

    return diff


@with_retry()
def delete_by_doc_id(tenant_id: str, bot_id: str, doc_id: str) -> int:
    """Delete all chunks where payload.doc_id == doc_id.

    Returns the number of points deleted. Returns 0 if collection
    doesn't exist or no points match.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()

    if not client.collection_exists(name):
        return 0

    selector = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
    count_before = client.count(name, count_filter=selector, exact=True).count
    client.delete(collection_name=name, points_selector=selector)
    logger.info(
        "qdrant_chunks_deleted",
        extra={
            "collection_name": name,
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "doc_id": doc_id,
            "count": count_before,
        },
    )
    return count_before


@with_retry()
def drop_collection(tenant_id: str, bot_id: str) -> bool:
    """Drop the entire collection. Returns True if dropped, False if it
    didn't exist. Used by tests + future v5 'delete entire bot' path.
    """
    name = collection_name(tenant_id, bot_id)
    client = get_qdrant_client()
    if not client.collection_exists(name):
        return False
    client.delete_collection(name)
    logger.info(
        "qdrant_collection_dropped",
        extra={"collection_name": name, "tenant_id": tenant_id, "bot_id": bot_id},
    )
    return True
