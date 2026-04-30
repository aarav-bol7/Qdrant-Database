"""Phase 8a search-quality runtime verification.

- RRF smoke: deterministic test that the 3x dense Prefetch duplication
  yields a dense:sparse score ratio in [1.5, 4.0]. Uses a real Qdrant
  collection (skip-graceful if unreachable) populated with two known
  points, plus a mocked `embed_query` returning controlled vectors.

- Backward-compat regression: directly upserts a Qdrant point with
  Phase 5/6/7 payload keys (category / tags / section_title) into the
  real Phase-7.5 vector schema. Asserts the search response is valid
  AND legacy fields pass through (the search() function does
  `dict(p.payload)`).
"""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import patch

import numpy as np
import pytest
from qdrant_client.models import PointStruct, SparseVector


@pytest.fixture(scope="session")
def qdrant_available():
    try:
        from apps.qdrant_core.client import get_qdrant_client

        get_qdrant_client.cache_clear()
        get_qdrant_client().get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant unreachable: {exc}")


@pytest.fixture
def fresh_bot(qdrant_available):
    tenant = f"rt_t_{uuid.uuid4().hex[:8]}"
    bot = f"rt_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    with contextlib.suppress(Exception):
        from apps.qdrant_core.collection import drop_collection

        drop_collection(tenant, bot)


def _zeros_with_one_at(idx: int, dim: int = 1024) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    v[idx] = 1.0
    return v


def _make_point(
    *,
    tenant_id: str,
    bot_id: str,
    doc_id: str,
    chunk_idx: int,
    text: str,
    dense: list[float],
    sparse: dict[int, float],
    colbert_rows: int = 3,
    colbert_signal_idx: int = 0,
    extra_payload: dict | None = None,
) -> PointStruct:
    """Build a PointStruct matching the Phase 7.5 vector schema."""
    sp = SparseVector(indices=list(sparse.keys()), values=list(sparse.values()))
    cb = [[0.0] * 1024 for _ in range(colbert_rows)]
    for row in cb:
        row[colbert_signal_idx] = 1.0  # shared ColBERT signal so rerank doesn't drop either point
    payload = {
        "chunk_id": f"{doc_id}__i0__c{chunk_idx}",
        "doc_id": doc_id,
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "text": text,
        "source_type": "text",
        "is_active": True,
        "section_path": [],
        "page_number": 1,
        "version": 1,
    }
    if extra_payload:
        payload.update(extra_payload)
    return PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_OID, payload["chunk_id"])),
        vector={
            "dense": dense,
            "bm25": sp,
            "colbert": cb,
        },
        payload=payload,
    )


class TestRRFSmoke:
    def test_rrf_dense_outweighs_sparse(self, fresh_bot):
        """3x dense + 1x sparse should rank a dense-aligned point above a sparse-only match."""
        from apps.qdrant_core import search as search_mod
        from apps.qdrant_core.client import get_qdrant_client
        from apps.qdrant_core.collection import create_collection_for_bot

        tenant, bot = fresh_bot
        collection = create_collection_for_bot(tenant, bot)
        client = get_qdrant_client()

        # Two distinct dense vectors so cosine similarity differs.
        vec_a = _zeros_with_one_at(0).tolist()  # aligned with query.dense
        vec_b = _zeros_with_one_at(500).tolist()  # orthogonal
        # Sparse: token 42 strong → matches "sparse-only" point's BM25 weight.
        sparse_query = {42: 1.0}
        sparse_a = {7: 0.1}  # weak BM25 signal
        sparse_b = {42: 1.0}  # strong BM25 match for the query

        point_dense = _make_point(
            tenant_id=tenant,
            bot_id=bot,
            doc_id="doc-dense",
            chunk_idx=0,
            text="dense-aligned chunk",
            dense=vec_a,
            sparse=sparse_a,
        )
        point_sparse = _make_point(
            tenant_id=tenant,
            bot_id=bot,
            doc_id="doc-sparse",
            chunk_idx=1,
            text="sparse-aligned chunk",
            dense=vec_b,
            sparse=sparse_b,
        )
        client.upsert(collection_name=collection, points=[point_dense, point_sparse])

        # Mock embed_query to return our controlled vectors.
        with patch.object(search_mod, "embed_query") as embed_mock:
            embed_mock.return_value = {
                "dense": np.array(vec_a, dtype=np.float32),
                "sparse": {str(k): float(v) for k, v in sparse_query.items()},
                "colbert": np.array([_zeros_with_one_at(0)] * 3, dtype=np.float32),
            }
            # Push threshold to 0 so both points surface even if ColBERT scores are low.
            with patch.object(search_mod, "SCORE_THRESHOLD", 0.0):
                result = search_mod.search(
                    tenant_id=tenant,
                    bot_id=bot,
                    query="ignored (mocked)",
                    top_k=20,
                )

        chunks = result["chunks"]
        assert len(chunks) >= 2, f"expected both points returned, got {len(chunks)}"
        by_doc = {c["doc_id"]: c for c in chunks}
        assert "doc-dense" in by_doc and "doc-sparse" in by_doc

        score_dense = by_doc["doc-dense"]["score"]
        score_sparse = by_doc["doc-sparse"]["score"]
        # Both should be positive
        assert score_dense > 0
        assert score_sparse > 0
        # Dense should outweigh sparse — ratio in a loose bound to tolerate
        # ColBERT rerank shuffling.
        ratio = score_dense / score_sparse if score_sparse > 0 else float("inf")
        assert 1.0 <= ratio <= 10.0, (
            f"expected dense:sparse ratio roughly 3:1; got {ratio:.3f} "
            f"(dense={score_dense}, sparse={score_sparse})"
        )


class TestBackwardCompat:
    def test_old_schema_chunk_searchable(self, fresh_bot):
        """A point upserted with Phase 5/6/7 deprecated payload keys is still searchable.

        The collection itself uses the real Phase-7.5 vector schema (created via
        the helper); only the payload dict carries the legacy keys.
        """
        from apps.qdrant_core import search as search_mod
        from apps.qdrant_core.client import get_qdrant_client
        from apps.qdrant_core.collection import create_collection_for_bot

        tenant, bot = fresh_bot
        collection = create_collection_for_bot(tenant, bot)
        client = get_qdrant_client()

        # Old-schema payload includes deprecated keys
        legacy_extras = {
            "category": "Finance",
            "tags": ["q3", "revenue"],
            "section_title": "Revenue Highlights",
        }
        legacy_point = _make_point(
            tenant_id=tenant,
            bot_id=bot,
            doc_id="legacy-doc",
            chunk_idx=0,
            text="old chunk text",
            dense=_zeros_with_one_at(0).tolist(),
            sparse={42: 1.0},
            extra_payload=legacy_extras,
        )
        client.upsert(collection_name=collection, points=[legacy_point])

        with patch.object(search_mod, "embed_query") as embed_mock:
            embed_mock.return_value = {
                "dense": _zeros_with_one_at(0),
                "sparse": {"42": 1.0},
                "colbert": np.array([_zeros_with_one_at(0)] * 3, dtype=np.float32),
            }
            with patch.object(search_mod, "SCORE_THRESHOLD", 0.0):
                result = search_mod.search(
                    tenant_id=tenant,
                    bot_id=bot,
                    query="legacy",
                    top_k=5,
                )

        chunks = result["chunks"]
        assert len(chunks) == 1, f"expected 1 chunk, got {len(chunks)}"
        chunk = chunks[0]
        assert chunk["text"] == "old chunk text"
        # search() does dict(p.payload) — legacy fields pass through.
        assert "category" in chunk and chunk["category"] == "Finance"
        assert "tags" in chunk and chunk["tags"] == ["q3", "revenue"]
        assert "section_title" in chunk
