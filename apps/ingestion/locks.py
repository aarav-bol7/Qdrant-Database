"""Postgres advisory lock context manager with acquire timeout."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Generator

from django.db import connection

from apps.documents.exceptions import ConcurrentUploadError
from apps.qdrant_core.naming import advisory_lock_key

logger = logging.getLogger(__name__)

DEFAULT_ACQUIRE_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05


@contextlib.contextmanager
def upload_lock(
    tenant_id: str,
    bot_id: str,
    doc_id: str,
    *,
    timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT_S,
) -> Generator[None]:
    """Try to acquire pg_advisory_lock; raise ConcurrentUploadError on timeout."""
    key1, key2 = advisory_lock_key(tenant_id, bot_id, doc_id)
    deadline = time.monotonic() + timeout_s
    acquired = False
    with connection.cursor() as cursor:
        while True:
            cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [key1, key2])
            row = cursor.fetchone()
            got = bool(row and row[0])
            if got:
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise ConcurrentUploadError(
                    (
                        f"Could not acquire lock for {tenant_id}/{bot_id}/{doc_id} "
                        f"within {timeout_s}s"
                    ),
                    retry_after=int(timeout_s),
                    details={
                        "tenant_id": tenant_id,
                        "bot_id": bot_id,
                        "doc_id": doc_id,
                    },
                )
            time.sleep(_POLL_INTERVAL_S)

        try:
            logger.debug(
                "advisory_lock_acquired",
                extra={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
            )
            yield
        finally:
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key1, key2])
                logger.debug(
                    "advisory_lock_released",
                    extra={"tenant_id": tenant_id, "bot_id": bot_id, "doc_id": doc_id},
                )
