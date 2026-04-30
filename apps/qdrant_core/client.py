"""Singleton gRPC QdrantClient with retry/backoff on transient failures.

The client is lazily constructed on first access (POST-fork in gunicorn
workers, avoiding fork-after-channel-create issues with gRPC). Each
worker process has its own client instance.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

import grpc
from django.conf import settings
from qdrant_client import QdrantClient

from apps.qdrant_core.exceptions import QdrantConnectionError

logger = logging.getLogger(__name__)

T = TypeVar("T")


@functools.lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Return the process-local QdrantClient singleton.

    Construction is lazy (first call). Subsequent calls in the same
    process return the cached instance. Each forked gunicorn worker
    builds its own.
    """
    cfg = settings.QDRANT
    return QdrantClient(
        host=cfg["HOST"],
        grpc_port=cfg["GRPC_PORT"],
        port=cfg["HTTP_PORT"],
        prefer_grpc=cfg["PREFER_GRPC"],
        api_key=cfg["API_KEY"] or None,
        https=False,
        timeout=10,
    )


_RETRYABLE_GRPC_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, grpc.RpcError):
        code_fn = getattr(exc, "code", None)
        if callable(code_fn):
            return code_fn() in _RETRYABLE_GRPC_CODES
        return False
    name = type(exc).__name__
    return name in {
        "ResponseHandlingException",
        "ConnectionError",
        "TimeoutException",
    }


def with_retry(
    *,
    attempts: int = 3,
    initial_delay: float = 0.5,
    backoff: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: retry on transient connection errors only.

    Schema-mismatch / 4xx-equivalent failures propagate immediately.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = initial_delay
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    if not _is_transient(exc):
                        raise
                    last_exc = exc
                    if attempt == attempts - 1:
                        break
                    sleep_for = delay * (1 + random.uniform(-0.2, 0.2))
                    logger.warning(
                        "qdrant_retry",
                        extra={
                            "attempt": attempt + 1,
                            "max_attempts": attempts,
                            "sleep_s": round(sleep_for, 3),
                            "exc_type": type(exc).__name__,
                        },
                    )
                    time.sleep(sleep_for)
                    delay *= backoff
            raise QdrantConnectionError(
                f"Exhausted {attempts} retry attempts: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator
