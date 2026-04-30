"""Liveness/readiness endpoint for the qdrant_rag service."""

from __future__ import annotations

import concurrent.futures
import functools
from typing import Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse

_VERSION = "0.1.0-dev"
_PROBE_TIMEOUT_S = 2

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="healthz")


@functools.lru_cache(maxsize=1)
def _get_qdrant_client() -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient(
        host=settings.QDRANT["HOST"],
        grpc_port=settings.QDRANT["GRPC_PORT"],
        port=settings.QDRANT["HTTP_PORT"],
        prefer_grpc=settings.QDRANT["PREFER_GRPC"],
        api_key=settings.QDRANT["API_KEY"] or None,
        https=False,
        timeout=_PROBE_TIMEOUT_S,
    )


def _ping_postgres() -> str:
    def _query() -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    try:
        future = _executor.submit(_query)
        future.result(timeout=_PROBE_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        return "error: timeout after 2s"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {str(exc)[:120]}"
    return "ok"


def _classify_qdrant_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    name = type(exc).__name__
    if "401" in msg or "unauthorized" in msg or "authentication" in msg:
        return "error: unauthorized — check QDRANT_API_KEY"
    if "unauthenticated" in msg:
        return "error: unauthorized — check QDRANT_API_KEY"
    return f"error: unreachable — {name}"


def _ping_qdrant() -> str:
    def _call() -> None:
        client = _get_qdrant_client()
        client.get_collections()

    try:
        future = _executor.submit(_call)
        future.result(timeout=_PROBE_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        return "error: timeout after 2s"
    except Exception as exc:
        return _classify_qdrant_error(exc)
    return "ok"


def healthz(_request: HttpRequest) -> JsonResponse:
    pg = _ping_postgres()
    qd = _ping_qdrant()
    all_ok = pg == "ok" and qd == "ok"
    body = {
        "status": "ok" if all_ok else "degraded",
        "version": _VERSION,
        "components": {
            "postgres": pg,
            "qdrant": qd,
        },
    }
    return JsonResponse(body, status=200 if all_ok else 503)
