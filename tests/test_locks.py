import threading

import pytest
from django.db import connection, connections

from apps.documents.exceptions import ConcurrentUploadError
from apps.ingestion.locks import upload_lock


def _require_postgres() -> None:
    if connection.vendor != "postgresql":
        pytest.skip(
            f"requires PostgreSQL (current vendor: {connection.vendor}); "
            "run via `docker compose exec web pytest tests/test_locks.py -v`"
        )


@pytest.mark.django_db(transaction=True)
class TestAdvisoryLock:
    def test_acquire_and_release(self):
        _require_postgres()
        with upload_lock("test_t", "test_b", "doc-1"):
            pass

    def test_concurrent_acquire_blocks(self):
        _require_postgres()
        result: dict[str, str] = {}

        def worker_b() -> None:
            try:
                with upload_lock("test_t", "test_b", "doc-1", timeout_s=1.0):
                    result["b"] = "acquired"
            except ConcurrentUploadError:
                result["b"] = "timeout"
            finally:
                connections.close_all()

        with upload_lock("test_t", "test_b", "doc-1"):
            t = threading.Thread(target=worker_b)
            t.start()
            t.join(timeout=3.0)

        assert result.get("b") == "timeout"

    def test_different_keys_dont_block(self):
        _require_postgres()
        result: dict[str, str] = {}

        def worker_b() -> None:
            try:
                with upload_lock("test_t", "test_b", "doc-different", timeout_s=1.0):
                    result["b"] = "acquired"
            except ConcurrentUploadError:
                result["b"] = "timeout"
            finally:
                connections.close_all()

        with upload_lock("test_t", "test_b", "doc-1"):
            t = threading.Thread(target=worker_b)
            t.start()
            t.join(timeout=3.0)

        assert result.get("b") == "acquired"
