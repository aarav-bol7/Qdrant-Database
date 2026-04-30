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
    """tests/test_settings.py uses in-memory SQLite which lacks
    pg_advisory_lock. The pipeline's upload_lock is exercised in 5b's
    test_locks.py against real Postgres; here we bypass it so the
    upload-pipeline integration tests run host-side.
    """

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


@pytest.mark.django_db
def test_201_fresh_upload(client, fresh_bot, embedder_available):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    response = client.post(url, body, format="json")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == "created"
    assert data["chunks_created"] >= 1
    assert data["items_processed"] == 2
    assert "doc_id" in data
    assert data["collection_name"] == f"t_{tenant}__b_{bot}"


@pytest.mark.django_db
def test_201_replace_existing(client, fresh_bot, embedder_available):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    r1 = client.post(url, body, format="json")
    assert r1.status_code == 201, r1.json()
    assert r1.json()["status"] == "created"
    body["content_hash"] = "sha256:second-different"
    r2 = client.post(url, body, format="json")
    assert r2.status_code == 201, r2.json()
    assert r2.json()["status"] == "replaced"


@pytest.mark.django_db
def test_400_invalid_tenant_slug(client):
    body = _load("valid_pdf_doc.json")
    response = client.post("/v1/tenants/Pizza-Palace/bots/sup/documents", body, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_slug"


@pytest.mark.django_db
def test_400_invalid_bot_slug(client):
    body = _load("valid_pdf_doc.json")
    response = client.post("/v1/tenants/pizzapalace/bots/Sup-Bot/documents", body, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_slug"


@pytest.mark.django_db
def test_201_when_source_type_omitted_uses_text_default(client, fresh_bot, embedder_available):
    """Phase 7.5: source_type defaults to 'text' when omitted."""
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    del body["source_type"]
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 201, response.json()
    # Verify the resulting Document row has source_type='text'
    from apps.documents.models import Document

    doc_id = response.json()["doc_id"]
    doc = Document.objects.get(doc_id=doc_id)
    assert doc.source_type == "text"


@pytest.mark.django_db
def test_400_when_top_level_language_present(client, fresh_bot):
    tenant, bot = fresh_bot
    body = {"source_type": "text", "items": [{"content": "x"}], "language": "en"}
    r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_payload"
    assert "removed" in str(r.json()).lower()


@pytest.mark.django_db
def test_400_when_custom_metadata_present(client, fresh_bot):
    tenant, bot = fresh_bot
    body = {"items": [{"content": "x"}], "custom_metadata": {"category": "x"}}
    r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert r.status_code == 400
    assert "removed" in str(r.json()).lower()


@pytest.mark.django_db
def test_400_when_item_url_present(client, fresh_bot):
    tenant, bot = fresh_bot
    body = {"items": [{"content": "x", "url": "https://example.com"}]}
    r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert r.status_code == 400
    assert "removed" in str(r.json()).lower()


@pytest.mark.django_db
def test_400_when_item_title_present(client, fresh_bot):
    tenant, bot = fresh_bot
    body = {"items": [{"content": "x", "title": "Section 1"}]}
    r = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_400_tenant_id_in_body(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["tenant_id"] = "evil_tenant"
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_payload"


@pytest.mark.django_db
def test_400_empty_items(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("invalid_no_items.json")
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_400_or_422_all_items_empty_content(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("invalid_empty_content.json")
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code in (400, 422)


@pytest.mark.django_db
def test_400_unsupported_source_type(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["source_type"] = "binary"
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_auto_creates_tenant_and_bot(client, fresh_bot, embedder_available):
    from apps.tenants.models import Bot, Tenant

    tenant, bot = fresh_bot
    assert not Tenant.objects.filter(tenant_id=tenant).exists()
    body = _load("valid_pdf_doc.json")
    response = client.post(f"/v1/tenants/{tenant}/bots/{bot}/documents", body, format="json")
    assert response.status_code == 201, response.json()
    assert Tenant.objects.filter(tenant_id=tenant).exists()
    bot_row = Bot.objects.get(tenant_id=tenant, bot_id=bot)
    from apps.documents.models import Document

    assert Document.objects.filter(bot_ref=bot_row).exists()


@pytest.mark.django_db
def test_chunks_have_full_payload_in_qdrant(client, fresh_bot, embedder_available):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from apps.qdrant_core.client import get_qdrant_client

    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    response = client.post(url, body, format="json")
    assert response.status_code == 201, response.json()
    doc_id = response.json()["doc_id"]

    client_q = get_qdrant_client()
    name = f"t_{tenant}__b_{bot}"
    points, _ = client_q.scroll(
        collection_name=name,
        scroll_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
        limit=10,
        with_payload=True,
    )
    assert len(points) >= 1
    p = points[0]
    payload = p.payload
    required = {
        "tenant_id",
        "bot_id",
        "doc_id",
        "chunk_id",
        "version",
        "is_active",
        "uploaded_at",
        "source_type",
        "source_filename",
        "source_url",
        "source_item_index",
        "source_content_hash",
        "section_path",
        "page_number",
        "text",
        "char_count",
        "token_count",
    }
    missing = required - set(payload.keys())
    assert not missing, f"Missing payload fields: {missing}"
    for dropped in ("section_title", "category", "tags"):
        assert dropped not in payload, f"{dropped} should not be in slim payload"
    assert payload["tenant_id"] == tenant
    assert payload["bot_id"] == bot
    assert payload["is_active"] is True
    assert payload["version"] == 1


@pytest.mark.django_db
def test_500_envelope_when_embedder_raises(client, fresh_bot):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    with patch(
        "apps.ingestion.pipeline.embed_passages",
        side_effect=RuntimeError("simulated embedder failure"),
    ):
        response = client.post(url, body, format="json")
    assert response.status_code == 500
    err = response.json()["error"]
    assert err["code"] == "embedder_failed"
    assert "message" in err


@pytest.mark.django_db
def test_500_envelope_when_qdrant_upsert_raises(client, fresh_bot, embedder_available):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"

    from apps.qdrant_core.client import get_qdrant_client

    real_client = get_qdrant_client()

    class FakeClient:
        def __getattr__(self, name):
            if name == "upsert":

                def boom(*a, **kw):
                    raise RuntimeError("simulated upsert failure")

                return boom
            return getattr(real_client, name)

    with patch("apps.ingestion.pipeline.get_qdrant_client", return_value=FakeClient()):
        response = client.post(url, body, format="json")
    assert response.status_code == 500
    err = response.json()["error"]
    assert err["code"] == "qdrant_write_failed"


@pytest.mark.django_db
def test_200_content_hash_short_circuit(client, fresh_bot, embedder_available):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    body["content_hash"] = "sha256:matching"
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"

    r1 = client.post(url, body, format="json")
    assert r1.status_code == 201, r1.json()
    assert r1.json()["status"] == "created"

    r2 = client.post(url, body, format="json")
    assert r2.status_code == 200, r2.json()
    assert r2.json()["status"] == "no_change"


@pytest.mark.django_db
def test_422_too_many_chunks(client, fresh_bot):
    tenant, bot = fresh_bot
    body = {
        "source_type": "faq",
        "items": [{"content": "Question? Answer text here."} for _ in range(5001)],
    }
    with patch(
        "apps.ingestion.chunker.count_tokens",
        side_effect=lambda t: max(1, len(t) // 4),
    ):
        response = client.post(
            f"/v1/tenants/{tenant}/bots/{bot}/documents",
            body,
            format="json",
        )
    assert response.status_code == 422, response.json()
    assert response.json()["error"]["code"] == "too_many_chunks"


@pytest.mark.django_db
def test_409_retry_after_header(client, fresh_bot):
    from apps.documents.exceptions import ConcurrentUploadError

    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"

    @contextlib.contextmanager
    def _raise_concurrent(*args, **kwargs):
        raise ConcurrentUploadError("busy", retry_after=7)
        yield

    with patch("apps.ingestion.pipeline.upload_lock", _raise_concurrent):
        response = client.post(url, body, format="json")
    assert response.status_code == 409, response.json()
    err = response.json()["error"]
    assert err["code"] == "concurrent_upload"
    assert response["Retry-After"] == "7"
