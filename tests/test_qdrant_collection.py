import contextlib
import uuid

import pytest

from apps.qdrant_core.client import get_qdrant_client
from apps.qdrant_core.collection import (
    COLBERT_VECTOR_SIZE,
    DENSE_VECTOR_SIZE,
    SPARSE_VECTOR_NAME,
    create_collection_for_bot,
    delete_by_doc_id,
    drop_collection,
    get_or_create_collection,
)
from apps.qdrant_core.exceptions import CollectionSchemaMismatchError


@pytest.fixture(scope="session")
def qdrant_available():
    try:
        get_qdrant_client.cache_clear()
        client = get_qdrant_client()
        client.get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable for integration tests: {exc}")


@pytest.fixture
def fresh_bot(qdrant_available):
    tenant = f"test_t_{uuid.uuid4().hex[:8]}"
    bot = f"test_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    with contextlib.suppress(Exception):
        drop_collection(tenant, bot)


class TestCreateCollection:
    def test_create_succeeds_with_locked_schema(self, fresh_bot):
        from qdrant_client.models import Modifier

        tenant, bot = fresh_bot
        name = create_collection_for_bot(tenant, bot)
        assert name == f"t_{tenant}__b_{bot}"

        client = get_qdrant_client()
        info = client.get_collection(name)
        assert info.config.params.vectors["dense"].size == DENSE_VECTOR_SIZE
        assert info.config.params.vectors["colbert"].size == COLBERT_VECTOR_SIZE
        assert info.config.params.vectors["colbert"].hnsw_config.m == 0
        assert SPARSE_VECTOR_NAME in (info.config.params.sparse_vectors or {})
        assert info.config.params.sparse_vectors[SPARSE_VECTOR_NAME].modifier == Modifier.IDF

    def test_payload_indexes_exist(self, fresh_bot):
        tenant, bot = fresh_bot
        name = create_collection_for_bot(tenant, bot)
        client = get_qdrant_client()
        info = client.get_collection(name)
        assert "doc_id" in info.payload_schema
        assert "is_active" in info.payload_schema


class TestGetOrCreateCollection:
    def test_idempotent(self, fresh_bot):
        tenant, bot = fresh_bot
        name1 = get_or_create_collection(tenant, bot)
        name2 = get_or_create_collection(tenant, bot)
        assert name1 == name2

    def test_schema_mismatch_raises(self, fresh_bot):
        from qdrant_client.models import Distance, VectorParams

        from apps.qdrant_core.naming import collection_name

        tenant, bot = fresh_bot
        name = collection_name(tenant, bot)
        client = get_qdrant_client()
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(size=512, distance=Distance.COSINE),
            },
        )
        with pytest.raises(CollectionSchemaMismatchError) as exc_info:
            get_or_create_collection(tenant, bot)
        assert "dense.size" in exc_info.value.diff


class TestDeleteByDocId:
    def test_returns_zero_when_collection_missing(self, fresh_bot):
        tenant, bot = fresh_bot
        assert delete_by_doc_id(tenant, bot, "any-doc-id") == 0

    def test_deletes_only_targeted_doc(self, fresh_bot):
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchValue,
            PointStruct,
            SparseVector,
        )

        tenant, bot = fresh_bot
        name = create_collection_for_bot(tenant, bot)
        client = get_qdrant_client()

        doc_a = "doc-aaa"
        doc_b = "doc-bbb"

        def _point(doc_id: str, idx: int) -> PointStruct:
            return PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    "dense": [0.0] * 1024,
                    "bm25": SparseVector(indices=[0], values=[0.1]),
                    "colbert": [[0.0] * 1024],
                },
                payload={"doc_id": doc_id, "is_active": True},
            )

        client.upsert(
            collection_name=name,
            points=[_point(doc_a, 0), _point(doc_a, 1), _point(doc_b, 0)],
        )
        deleted = delete_by_doc_id(tenant, bot, doc_a)
        assert deleted == 2

        remaining = client.count(
            name,
            count_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_b))]),
            exact=True,
        )
        assert remaining.count == 1


class TestDropCollection:
    def test_returns_true_when_dropped(self, fresh_bot):
        tenant, bot = fresh_bot
        create_collection_for_bot(tenant, bot)
        assert drop_collection(tenant, bot) is True

    def test_returns_false_when_missing(self, fresh_bot):
        tenant, bot = fresh_bot
        assert drop_collection(tenant, bot) is False
