import contextlib
import os
import uuid

import grpc
import pytest


@pytest.fixture(scope="session")
def grpc_channel():
    host = os.environ.get("GRPC_HOST", "localhost")
    port = int(os.environ.get("GRPC_PORT", "50051"))
    addr = f"{host}:{port}"
    channel = grpc.insecure_channel(addr)
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
    except grpc.FutureTimeoutError:
        pytest.skip(f"gRPC server not reachable at {addr}")
    yield channel
    channel.close()


@pytest.fixture(scope="session")
def search_stub(grpc_channel):
    from apps.grpc_service.generated import search_pb2_grpc

    return search_pb2_grpc.VectorSearchStub(grpc_channel)


@pytest.fixture
def fresh_bot():
    tenant = f"test_t_{uuid.uuid4().hex[:8]}"
    bot = f"test_b_{uuid.uuid4().hex[:8]}"
    yield tenant, bot
    with contextlib.suppress(Exception):
        from apps.qdrant_core.collection import drop_collection

        drop_collection(tenant, bot)


def _request(**kwargs):
    from apps.grpc_service.generated import search_pb2

    only_active = kwargs.pop("only_active", True)
    source_types = kwargs.pop("source_types", None)
    tags = kwargs.pop("tags", None)
    category = kwargs.pop("category", "")

    filters = search_pb2.Filters(
        only_active=only_active,
        source_types=source_types or [],
        tags=tags or [],
        category=category,
    )
    return search_pb2.SearchRequest(filters=filters, **kwargs)


class TestHealthCheck:
    def test_health_check_returns_versioned_response(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        response = search_stub.HealthCheck(search_pb2.HealthCheckRequest(), timeout=5)
        assert response.version == "0.1.0-dev"

    def test_health_check_qdrant_ok_when_stack_up(self, search_stub):
        from apps.grpc_service.generated import search_pb2

        response = search_stub.HealthCheck(search_pb2.HealthCheckRequest(), timeout=5)
        assert response.qdrant_ok is True


class TestSearchValidation:
    def test_invalid_argument_on_empty_query(self, search_stub):
        request = _request(tenant_id="test_t", bot_id="test_b", query="")
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_whitespace_only_query(self, search_stub):
        request = _request(tenant_id="test_t", bot_id="test_b", query="   \n\t ")
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_bad_tenant_slug(self, search_stub):
        request = _request(tenant_id="Bad-Tenant", bot_id="test_b", query="x")
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_bad_bot_slug(self, search_stub):
        request = _request(tenant_id="test_t", bot_id="Bad-Bot", query="x")
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_top_k_too_high(self, search_stub):
        request = _request(tenant_id="test_t", bot_id="test_b", query="x", top_k=999)
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_on_top_k_negative(self, search_stub):
        request = _request(tenant_id="test_t", bot_id="test_b", query="x", top_k=-1)
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_invalid_argument_when_only_active_false(self, search_stub):
        request = _request(tenant_id="test_t", bot_id="test_b", query="x", only_active=False)
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=5)
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


class TestSearchNotFound:
    def test_not_found_when_collection_missing(self, search_stub, fresh_bot):
        tenant, bot = fresh_bot
        request = _request(tenant_id=tenant, bot_id=bot, query="hello")
        with pytest.raises(grpc.RpcError) as exc_info:
            search_stub.Search(request, timeout=30)
        assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


class TestCrossTenantIsolation:
    def test_search_in_tenant_b_cannot_see_tenant_a_collection(self, search_stub, fresh_bot):
        from apps.qdrant_core.collection import create_collection_for_bot

        tenant_a, bot_a = fresh_bot
        # Create a collection in tenant_a only
        create_collection_for_bot(tenant_a, bot_a)

        # Search in a totally different (tenant_b, bot_b) — collection absent
        tenant_b = f"test_t_{uuid.uuid4().hex[:8]}"
        bot_b = f"test_b_{uuid.uuid4().hex[:8]}"
        try:
            request = _request(tenant_id=tenant_b, bot_id=bot_b, query="hello")
            with pytest.raises(grpc.RpcError) as exc_info:
                search_stub.Search(request, timeout=30)
            assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
        finally:
            with contextlib.suppress(Exception):
                from apps.qdrant_core.collection import drop_collection

                drop_collection(tenant_b, bot_b)
