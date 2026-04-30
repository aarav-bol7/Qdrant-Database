import contextlib
import time
import uuid
from unittest.mock import patch

import numpy as np
import pytest

from apps.documents.exceptions import (
    DocumentTooLargeError,
    NoEmbeddableContentError,
)
from apps.documents.models import Document
from apps.ingestion.pipeline import UploadPipeline


def _doc_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _bypass_pg_advisory_lock():
    """tests.test_settings uses in-memory SQLite which lacks pg_advisory_lock.
    The lock helper itself is exercised by tests/test_locks.py against real
    Postgres; here we bypass it so pipeline-logic tests run host-side.
    """

    @contextlib.contextmanager
    def _noop(*args, **kwargs):
        yield

    with patch("apps.ingestion.pipeline.upload_lock", _noop):
        yield


def _mock_embeddings(n: int) -> dict:
    return {
        "dense": [np.zeros(1024, dtype=np.float32) for _ in range(n)],
        "sparse": [{"42": 0.5} for _ in range(n)],
        "colbert": [np.zeros((3, 1024), dtype=np.float32) for _ in range(n)],
    }


@pytest.fixture
def mock_embedder():
    with (
        patch("apps.ingestion.pipeline.embed_passages") as embed_mock,
        patch("apps.ingestion.pipeline.get_qdrant_client") as client_mock,
        patch("apps.ingestion.pipeline.get_or_create_collection") as goc_mock,
        patch("apps.ingestion.pipeline.delete_by_doc_id") as del_mock,
        patch(
            "apps.ingestion.chunker.count_tokens",
            side_effect=lambda t: max(1, len(t) // 4),
        ),
    ):
        embed_mock.side_effect = lambda texts: _mock_embeddings(len(texts))
        goc_mock.return_value = "t_test_t__b_test_b"
        del_mock.return_value = 0
        yield {
            "embed": embed_mock,
            "client": client_mock.return_value,
            "goc": goc_mock,
            "del": del_mock,
        }


def _body(items_count: int = 1, content_hash: str = "sha256:abc") -> dict:
    return {
        "source_type": "pdf",
        "source_filename": "x.pdf",
        "content_hash": content_hash,
        "items": [{"content": f"Some content for item {i}." * 30} for i in range(items_count)],
    }


@pytest.mark.django_db
class TestContentHashShortCircuit:
    def test_no_change_when_content_hash_matches_and_chunks_exist(self, mock_embedder):
        body = _body(items_count=1)
        d = _doc_id()
        r1 = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body)
        assert r1.status == "created"

        before = Document.objects.get(doc_id=d).last_refreshed_at
        time.sleep(0.01)

        r2 = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body)
        assert r2.status == "no_change"
        assert mock_embedder["embed"].call_count == 1

        after = Document.objects.get(doc_id=d).last_refreshed_at
        assert after > before

    def test_full_pipeline_when_content_hash_differs(self, mock_embedder):
        d = _doc_id()
        body1 = _body(content_hash="sha256:aaa")
        UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body1)
        body2 = _body(content_hash="sha256:bbb")
        r = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body2)
        assert r.status == "replaced"
        assert mock_embedder["embed"].call_count == 2

    def test_no_change_when_content_hash_absent_and_content_matches(self, mock_embedder):
        d = _doc_id()
        body1 = _body(content_hash="")
        UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body1)
        body2 = _body(content_hash="")
        r = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body2)
        assert r.status == "no_change"
        assert mock_embedder["embed"].call_count == 1

    def test_no_change_across_doc_ids_when_content_matches(self, mock_embedder):
        body = _body(content_hash="")
        d1 = _doc_id()
        r1 = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d1, body=body)
        assert r1.status == "created"

        d2 = _doc_id()
        r2 = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d2, body=body)
        assert r2.status == "no_change"
        assert r2.doc_id == d1
        assert mock_embedder["embed"].call_count == 1


@pytest.mark.django_db
class TestChunkCap:
    def test_too_many_chunks_raises_document_too_large(self, mock_embedder):
        with patch(
            "apps.ingestion.chunker.count_tokens",
            side_effect=lambda t: max(1, len(t) // 4),
        ):
            body = {
                "source_type": "faq",
                "items": [{"content": "Q? A."} for _ in range(5001)],
            }
            with pytest.raises(DocumentTooLargeError):
                UploadPipeline.execute(
                    tenant_id="test_t",
                    bot_id="test_b",
                    doc_id=_doc_id(),
                    body=body,
                )
        assert mock_embedder["client"].upsert.call_count == 0


@pytest.mark.django_db
class TestNoEmbeddableContent:
    def test_all_empty_content_raises(self, mock_embedder):
        body = {
            "source_type": "pdf",
            "items": [{"content": "   "}],
        }
        with pytest.raises(NoEmbeddableContentError):
            UploadPipeline.execute(
                tenant_id="test_t",
                bot_id="test_b",
                doc_id=_doc_id(),
                body=body,
            )


@pytest.mark.django_db
class TestRawPayloadPersistence:
    def test_raw_payload_persists_full_body(self, mock_embedder):
        d = _doc_id()
        body = _body(items_count=1, content_hash="sha256:rp1")
        body["items"][0]["content"] = "raw_payload test 1 unique content"
        UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body)
        doc = Document.objects.get(doc_id=d)
        assert doc.raw_payload is not None
        assert doc.raw_payload == body

    def test_raw_payload_unchanged_on_no_change(self, mock_embedder):
        d = _doc_id()
        body = _body(items_count=1, content_hash="sha256:rp2")
        body["items"][0]["content"] = "raw_payload test 2 unique content"
        UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body)
        v1 = Document.objects.get(doc_id=d).raw_payload
        assert v1 == body

        r2 = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body)
        assert r2.status == "no_change"
        assert Document.objects.get(doc_id=d).raw_payload == v1

    def test_raw_payload_overwritten_on_replace(self, mock_embedder):
        d = _doc_id()
        body1 = _body(items_count=1, content_hash="sha256:rp3a")
        body1["items"][0]["content"] = "raw_payload test 3 v1"
        UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body1)

        body2 = _body(items_count=1, content_hash="sha256:rp3b")
        body2["items"][0]["content"] = "raw_payload test 3 v2 different"
        r2 = UploadPipeline.execute(tenant_id="test_t", bot_id="test_b", doc_id=d, body=body2)
        assert r2.status == "replaced"
        doc = Document.objects.get(doc_id=d)
        assert doc.raw_payload == body2
        assert doc.raw_payload["items"][0]["content"] == "raw_payload test 3 v2 different"
