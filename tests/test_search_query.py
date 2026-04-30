from unittest.mock import MagicMock, patch

import numpy as np
import pytest
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

from apps.qdrant_core.collection import (
    COLBERT_VECTOR_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
)
from apps.qdrant_core.search import (
    DEFAULT_TOP_K,
    FUSION_LIMIT,
    MAX_TOP_K,
    PREFETCH_LIMIT,
    RRF_DENSE_WEIGHT,
    RRF_SPARSE_WEIGHT,
    SCORE_THRESHOLD,
    CollectionNotFoundError,
    search,
)


@pytest.fixture
def mock_deps():
    with (
        patch("apps.qdrant_core.search.get_qdrant_client") as client_mock,
        patch("apps.qdrant_core.search.embed_query") as embed_mock,
    ):
        client = client_mock.return_value
        client.collection_exists.return_value = True
        client.query_points.return_value = MagicMock(points=[])

        embed_mock.return_value = {
            "dense": np.zeros(1024, dtype=np.float32),
            "sparse": {"42": 0.5, "100": 0.1},
            "colbert": np.zeros((3, 1024), dtype=np.float32),
        }
        yield {"client": client, "embed": embed_mock}


def _call_kwargs(mock_deps):
    return mock_deps["client"].query_points.call_args.kwargs


class TestSearchHappyPath:
    def test_calls_qdrant_with_correct_collection_name(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert _call_kwargs(mock_deps)["collection_name"] == "t_test_t__b_test_b"

    def test_uses_default_top_k(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert _call_kwargs(mock_deps)["limit"] == DEFAULT_TOP_K

    def test_max_top_k_constant_is_20(self):
        assert MAX_TOP_K == 20

    def test_score_threshold_passes_through(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert _call_kwargs(mock_deps)["score_threshold"] == SCORE_THRESHOLD
        assert SCORE_THRESHOLD == 0.0

    def test_with_vectors_is_false(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert _call_kwargs(mock_deps)["with_vectors"] is False

    def test_with_payload_is_true(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert _call_kwargs(mock_deps)["with_payload"] is True

    def test_query_uses_colbert_vector_name(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert _call_kwargs(mock_deps)["using"] == COLBERT_VECTOR_NAME

    def test_top_level_prefetch_has_one_outer_node(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        prefetch = _call_kwargs(mock_deps)["prefetch"]
        assert isinstance(prefetch, list)
        assert len(prefetch) == 1
        assert isinstance(prefetch[0], Prefetch)

    def test_outer_prefetch_uses_rrf_fusion(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        assert isinstance(outer.query, FusionQuery)
        assert outer.query.fusion == Fusion.RRF

    def test_outer_prefetch_limit_is_fusion_limit(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        assert outer.limit == FUSION_LIMIT

    def test_inner_prefetches_have_3_dense_and_1_sparse(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        inner = outer.prefetch
        assert len(inner) == RRF_DENSE_WEIGHT + RRF_SPARSE_WEIGHT == 4

        dense_count = sum(1 for p in inner if p.using == DENSE_VECTOR_NAME)
        sparse_count = sum(1 for p in inner if p.using == SPARSE_VECTOR_NAME)
        assert dense_count == RRF_DENSE_WEIGHT == 3
        assert sparse_count == RRF_SPARSE_WEIGHT == 1

    def test_inner_dense_prefetches_use_same_vector(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        dense = [p for p in outer.prefetch if p.using == DENSE_VECTOR_NAME]
        assert len(dense) == RRF_DENSE_WEIGHT
        # all three dense prefetches use the same query vector
        first_query = dense[0].query
        for d in dense[1:]:
            assert d.query == first_query

    def test_inner_sparse_uses_sparsevector(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        sparse = next(p for p in outer.prefetch if p.using == SPARSE_VECTOR_NAME)
        assert isinstance(sparse.query, SparseVector)

    def test_inner_prefetches_have_prefetch_limit(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        for p in outer.prefetch:
            assert p.limit == PREFETCH_LIMIT == 50

    def test_inner_prefetches_carry_is_active_filter(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        outer = _call_kwargs(mock_deps)["prefetch"][0]
        for p in outer.prefetch:
            assert isinstance(p.filter, Filter)
            assert any(
                isinstance(c, FieldCondition)
                and c.key == "is_active"
                and isinstance(c.match, MatchValue)
                and c.match.value is True
                for c in p.filter.must
            )

    def test_final_query_carries_is_active_filter(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        qfilter = _call_kwargs(mock_deps)["query_filter"]
        assert isinstance(qfilter, Filter)
        assert any(
            isinstance(c, FieldCondition)
            and c.key == "is_active"
            and isinstance(c.match, MatchValue)
            and c.match.value is True
            for c in qfilter.must
        )


class TestCollectionNotFound:
    def test_raises_when_collection_missing(self, mock_deps):
        mock_deps["client"].collection_exists.return_value = False
        with pytest.raises(CollectionNotFoundError):
            search(tenant_id="test_t", bot_id="test_b", query="hello")

    def test_does_not_call_query_points_when_missing(self, mock_deps):
        mock_deps["client"].collection_exists.return_value = False
        with pytest.raises(CollectionNotFoundError):
            search(tenant_id="test_t", bot_id="test_b", query="hello")
        mock_deps["client"].query_points.assert_not_called()


class TestSearchEmptyResults:
    def test_empty_points_returns_empty_chunks(self, mock_deps):
        mock_deps["client"].query_points.return_value = MagicMock(points=[])
        result = search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert result["chunks"] == []
        assert result["total_candidates"] == 0
        assert result["threshold_used"] == SCORE_THRESHOLD

    def test_does_not_raise_or_treat_empty_as_error(self, mock_deps):
        mock_deps["client"].query_points.return_value = MagicMock(points=[])
        # No exception, plain return
        result = search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert isinstance(result, dict)


class TestFilterComposition:
    def test_no_optional_filters_only_is_active(self, mock_deps):
        search(tenant_id="test_t", bot_id="test_b", query="hello")
        qfilter = _call_kwargs(mock_deps)["query_filter"]
        keys = [c.key for c in qfilter.must]
        assert keys == ["is_active"]

    def test_source_types_adds_match_any(self, mock_deps):
        search(
            tenant_id="test_t",
            bot_id="test_b",
            query="hello",
            source_types=["pdf", "url"],
        )
        qfilter = _call_kwargs(mock_deps)["query_filter"]
        st = next(c for c in qfilter.must if c.key == "source_type")
        assert isinstance(st.match, MatchAny)
        assert sorted(st.match.any) == ["pdf", "url"]

    def test_tags_adds_match_any(self, mock_deps):
        search(
            tenant_id="test_t",
            bot_id="test_b",
            query="hello",
            tags=["refund", "delivery"],
        )
        qfilter = _call_kwargs(mock_deps)["query_filter"]
        tag = next(c for c in qfilter.must if c.key == "tags")
        assert isinstance(tag.match, MatchAny)
        assert sorted(tag.match.any) == ["delivery", "refund"]

    def test_category_adds_match_value(self, mock_deps):
        search(
            tenant_id="test_t",
            bot_id="test_b",
            query="hello",
            category="policy",
        )
        qfilter = _call_kwargs(mock_deps)["query_filter"]
        cat = next(c for c in qfilter.must if c.key == "category")
        assert isinstance(cat.match, MatchValue)
        assert cat.match.value == "policy"

    def test_all_optional_filters_compose(self, mock_deps):
        search(
            tenant_id="test_t",
            bot_id="test_b",
            query="hello",
            source_types=["pdf"],
            tags=["refund"],
            category="policy",
        )
        qfilter = _call_kwargs(mock_deps)["query_filter"]
        keys = sorted(c.key for c in qfilter.must)
        assert keys == ["category", "is_active", "source_type", "tags"]


class TestPayloadShape:
    def test_score_added_to_payload(self, mock_deps):
        point = MagicMock()
        point.payload = {"chunk_id": "c1", "doc_id": "d1", "text": "t"}
        point.score = 0.91
        mock_deps["client"].query_points.return_value = MagicMock(points=[point])

        result = search(tenant_id="test_t", bot_id="test_b", query="hello")
        assert len(result["chunks"]) == 1
        assert result["chunks"][0]["score"] == pytest.approx(0.91)
        assert result["chunks"][0]["chunk_id"] == "c1"
