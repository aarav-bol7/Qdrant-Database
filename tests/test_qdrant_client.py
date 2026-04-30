import grpc
import pytest

from apps.qdrant_core.client import _is_transient, get_qdrant_client, with_retry
from apps.qdrant_core.exceptions import (
    CollectionSchemaMismatchError,
    QdrantConnectionError,
)


class TestSingleton:
    def test_returns_same_instance(self):
        get_qdrant_client.cache_clear()
        a = get_qdrant_client()
        b = get_qdrant_client()
        assert a is b

    def test_cache_clear_reinitializes(self):
        get_qdrant_client.cache_clear()
        a = get_qdrant_client()
        get_qdrant_client.cache_clear()
        b = get_qdrant_client()
        assert a is not b


class TestIsTransient:
    def test_unavailable_is_transient(self):
        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

        assert _is_transient(FakeRpcError())

    def test_invalid_argument_is_not_transient(self):
        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.INVALID_ARGUMENT

        assert not _is_transient(FakeRpcError())

    def test_value_error_is_not_transient(self):
        assert not _is_transient(ValueError("nope"))


class TestRetryDecorator:
    def test_succeeds_first_try(self):
        calls = []

        @with_retry(attempts=3, initial_delay=0.01)
        def f():
            calls.append(1)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        calls = []

        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

        @with_retry(attempts=3, initial_delay=0.01)
        def f():
            calls.append(1)
            if len(calls) < 2:
                raise FakeRpcError()
            return "ok"

        assert f() == "ok"
        assert len(calls) == 2

    def test_exhausted_retries_raise_connection_error(self):
        class FakeRpcError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

        @with_retry(attempts=2, initial_delay=0.01)
        def f():
            raise FakeRpcError()

        with pytest.raises(QdrantConnectionError):
            f()

    def test_non_transient_propagates_immediately(self):
        calls = []

        @with_retry(attempts=3, initial_delay=0.01)
        def f():
            calls.append(1)
            raise CollectionSchemaMismatchError("c", {"x": "y"})

        with pytest.raises(CollectionSchemaMismatchError):
            f()
        assert len(calls) == 1
