import contextlib
import json
import pathlib
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _bypass_pg_advisory_lock_for_sqlite_tests():
    @contextlib.contextmanager
    def _noop(*args, **kwargs):
        yield

    with patch("apps.ingestion.pipeline.upload_lock", _noop):
        yield


@pytest.fixture(scope="session")
def qdrant_available():
    try:
        from apps.qdrant_core.client import get_qdrant_client

        get_qdrant_client.cache_clear()
        get_qdrant_client().get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant unreachable: {exc}")


@pytest.fixture(scope="session")
def embedder_available():
    try:
        from apps.ingestion.embedder import _get_model

        _get_model()
    except Exception as exc:
        pytest.skip(f"BGE-M3 cannot load in this environment: {exc}")


@pytest.fixture
def fresh_bot(qdrant_available):
    tenant = f"test_t_{uuid.uuid4().hex[:8]}"
    bot = f"test_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    try:
        from apps.qdrant_core.collection import drop_collection

        drop_collection(tenant, bot)
    except Exception:
        pass


@pytest.fixture
def client():
    return APIClient()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def uploaded_doc(client, fresh_bot, embedder_available):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert r.status_code == 201, r.json()
    return tenant, bot, body["doc_id"]


@pytest.mark.django_db
class TestSearchHttpHappyPath:
    def test_200_search_returns_chunks_shape(self, client, uploaded_doc):
        tenant, bot, _doc_id = uploaded_doc
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "cold pizza refund"}, format="json")
        assert r.status_code == 200, r.json()
        data = r.json()
        assert "chunks" in data
        assert "total_candidates" in data
        assert "threshold_used" in data
        for chunk in data["chunks"]:
            assert "chunk_id" in chunk
            assert "text" in chunk
            assert "score" in chunk
            assert chunk["score"] >= 0.0

    def test_default_top_k_is_5(self, client, uploaded_doc):
        tenant, bot, _ = uploaded_doc
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "refund"}, format="json")
        assert r.status_code == 200
        assert len(r.json()["chunks"]) <= 5


@pytest.mark.django_db
class TestSearchHttpValidation:
    def test_400_empty_query(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": ""}, format="json")
        assert r.status_code == 400

    def test_400_whitespace_only_query(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "   "}, format="json")
        assert r.status_code == 400

    def test_400_bad_tenant_slug(self, client):
        r = client.post(
            "/v1/tenants/Bad-Tenant/bots/sup/search",
            {"query": "x"},
            format="json",
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_slug"

    def test_400_bad_bot_slug(self, client):
        r = client.post(
            "/v1/tenants/sup/bots/Bad-Bot/search",
            {"query": "x"},
            format="json",
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_slug"

    def test_400_top_k_too_high(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "x", "top_k": 99}, format="json")
        assert r.status_code == 400

    def test_400_top_k_negative(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "x", "top_k": -1}, format="json")
        assert r.status_code == 400

    def test_400_only_active_false(self, client, fresh_bot):
        tenant, bot = fresh_bot
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(
            url,
            {"query": "x", "filters": {"only_active": False}},
            format="json",
        )
        assert r.status_code == 400


@pytest.mark.django_db
class TestSearchHttpNotFound:
    def test_404_when_collection_missing(self, client, qdrant_available):
        tenant = f"never_{uuid.uuid4().hex[:8]}"
        bot = f"never_{uuid.uuid4().hex[:8]}"
        url = f"/v1/tenants/{tenant}/bots/{bot}/search"
        r = client.post(url, {"query": "x"}, format="json")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "collection_not_found"
