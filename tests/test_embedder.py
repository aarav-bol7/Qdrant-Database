import numpy as np
import pytest

pytestmark = pytest.mark.embedder


@pytest.fixture(scope="session", autouse=True)
def model_loadable():
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
        assert 1 < n < 10


class TestEmbedPassages:
    def test_returns_three_vector_types(self):
        from apps.ingestion.embedder import (
            COLBERT_DIM,
            DENSE_DIM,
            embed_passages,
        )

        out = embed_passages(["First sentence.", "Second sentence."])

        assert len(out["dense"]) == 2
        assert len(out["dense"][0]) == DENSE_DIM
        assert len(out["sparse"]) == 2
        assert isinstance(out["sparse"][0], dict)
        assert len(out["sparse"][0]) > 0
        assert len(out["colbert"]) == 2
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
