"""Embedder warmup state.

Set to True once the BGE-M3 model has produced one successful forward pass
on this process. Read by /healthz to gate Docker readiness so the first
real user request lands on a warm model instead of paying the ~1-2s
first-encode tax.

Failures are logged at WARNING and swallowed — warmup MUST NOT block the
HTTP server from accepting connections (otherwise a missing weights file
would deadlock the container at startup forever). When warmup fails,
``is_embedder_loaded()`` stays False, /healthz reports 503, and the
container never becomes "healthy" — operator sees the failure in logs.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_EMBEDDER_LOADED: bool = False
_WARMUP_STARTED: bool = False
_LOCK = threading.Lock()


def is_embedder_loaded() -> bool:
    """Public accessor used by /healthz."""
    return _EMBEDDER_LOADED


def _do_warmup() -> None:
    """Synchronous warmup body. Runs in a daemon thread.

    Issues a single ``embed_query("warmup")`` so BGE-M3's first forward pass
    (model load + dense + sparse + ColBERT) lands here, not on the first user
    request. Sets ``_EMBEDDER_LOADED = True`` on success.
    """
    global _EMBEDDER_LOADED
    try:
        from apps.ingestion.embedder import embed_query

        t0 = time.perf_counter()
        _ = embed_query("warmup")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _EMBEDDER_LOADED = True
        logger.info(
            "embedder: warmup ok elapsed_ms=%.1f", elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "embedder: warmup failed (non-fatal): %s", exc, exc_info=True,
        )
        # Leave _EMBEDDER_LOADED = False. Healthz keeps reporting not-ready;
        # the next real request to embed will retry the natural lazy-init path.


def start_warmup_in_background() -> None:
    """Start the warmup thread once. Idempotent — repeat calls are no-ops.

    Daemon thread so it does not block process shutdown if the model load
    is mid-flight when the container is stopped.
    """
    global _WARMUP_STARTED
    with _LOCK:
        if _WARMUP_STARTED:
            return
        _WARMUP_STARTED = True
    t = threading.Thread(target=_do_warmup, name="embedder-warmup", daemon=True)
    t.start()
    logger.info("embedder: warmup thread started")


def _reset_for_tests() -> None:
    """Reset module state — for unit tests only."""
    global _EMBEDDER_LOADED, _WARMUP_STARTED
    with _LOCK:
        _EMBEDDER_LOADED = False
        _WARMUP_STARTED = False
