import contextlib
import json
import pathlib
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.documents.models import Document

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _bypass_pg_advisory_lock_for_sqlite_tests():
    """tests/test_settings.py uses in-memory SQLite which lacks
    pg_advisory_lock. The pipeline's upload_lock is exercised in 5b's
    test_locks.py against real Postgres; here we bypass it so the
    delete-pipeline integration tests run host-side.
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


@pytest.fixture
def uploaded_doc(client, fresh_bot, embedder_available):
    tenant, bot = fresh_bot
    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents"
    r = client.post(url, body, format="json")
    assert r.status_code == 201, r.json()
    return tenant, bot, body["doc_id"]


@pytest.mark.django_db
def test_204_delete_existing_doc(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc
    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}")
    assert response.status_code == 204
    assert not response.content


@pytest.mark.django_db
def test_204_idempotent_redelete(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc
    url = f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}"
    r1 = client.delete(url)
    assert r1.status_code == 204
    assert not r1.content
    r2 = client.delete(url)
    assert r2.status_code == 204
    assert not r2.content


@pytest.mark.django_db
def test_404_nonexistent_doc(client, fresh_bot):
    tenant, bot = fresh_bot
    random_uuid = str(uuid.uuid4())
    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{random_uuid}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


@pytest.mark.django_db
def test_400_invalid_tenant_slug(client):
    random_uuid = str(uuid.uuid4())
    response = client.delete(f"/v1/tenants/Pizza-Palace/bots/sup/documents/{random_uuid}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_slug"


@pytest.mark.django_db
def test_400_invalid_bot_slug(client):
    random_uuid = str(uuid.uuid4())
    response = client.delete(f"/v1/tenants/pizzapalace/bots/Bad-Bot/documents/{random_uuid}")
    assert response.status_code == 400


@pytest.mark.django_db
def test_404_malformed_uuid_returns_django_404(client, fresh_bot):
    tenant, bot = fresh_bot
    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/not-a-uuid")
    assert response.status_code == 404


@pytest.mark.django_db
def test_document_soft_deleted_in_postgres(client, uploaded_doc):
    tenant, bot, doc_id = uploaded_doc
    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}")
    assert response.status_code == 204

    doc = Document.objects.get(doc_id=doc_id)
    assert doc.status == Document.DELETED
    assert doc.chunk_count == 0


@pytest.mark.django_db
def test_qdrant_chunks_gone_after_delete(client, uploaded_doc):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from apps.qdrant_core.client import get_qdrant_client

    tenant, bot, doc_id = uploaded_doc
    qclient = get_qdrant_client()
    name = f"t_{tenant}__b_{bot}"
    before = qclient.count(
        name,
        count_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
        exact=True,
    ).count
    assert before > 0

    response = client.delete(f"/v1/tenants/{tenant}/bots/{bot}/documents/{doc_id}")
    assert response.status_code == 204

    after = qclient.count(
        name,
        count_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
        exact=True,
    ).count
    assert after == 0


@pytest.mark.django_db
def test_404_cross_tenant_doc_id(client, fresh_bot, embedder_available):
    """If a doc_id exists in tenant_a, deleting from tenant_b returns 404.

    tenant_b is never auto-created on DELETE (only POST auto-creates);
    the lookup finds no Document in tenant_b's slug-space, so 404. The
    cleanup drop_collection on tenant_b is defensive — no collection
    should have been created.
    """
    tenant_a, bot_a = fresh_bot
    tenant_b = f"test_t2_{uuid.uuid4().hex[:8]}"
    bot_b = f"test_b2_{uuid.uuid4().hex[:8]}"

    body = _load("valid_pdf_doc.json")
    body["doc_id"] = str(uuid.uuid4())
    r1 = client.post(f"/v1/tenants/{tenant_a}/bots/{bot_a}/documents", body, format="json")
    assert r1.status_code == 201

    response = client.delete(f"/v1/tenants/{tenant_b}/bots/{bot_b}/documents/{body['doc_id']}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"

    try:
        from apps.qdrant_core.collection import drop_collection

        drop_collection(tenant_b, bot_b)
    except Exception:
        pass
