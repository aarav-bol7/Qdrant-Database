"""Request-id + per-request access-log middleware.

RequestIDMiddleware reads/generates `X-Request-ID`, sets a ContextVar for
the duration of the request (with token-based reset to prevent leakage
across requests), and echoes the id in the response header.

AccessLogMiddleware emits exactly one structured `request_completed` log
line per request with method/path/status/duration_ms and per-phase
timings, then records HTTP/pipeline counters.

Phase 8b polish:
- RequestID exclusion narrowed to /static/ only (correlation IDs help
  for /healthz and /admin/; /metrics scrapers ignore the header).
- Access-log exclusion stays {/metrics, /healthz, /static/, /admin/}.
- Counter recorder calls fire from AccessLog finally block AFTER the
  access log emit (so they obey the same exclusion list).
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from apps.core.metrics_recorders import record_http_request, record_pipeline_phase
from apps.core.timing import reset_phase_durations

logger = logging.getLogger("apps.core.access")

_MAX_REQUEST_ID_LEN = 100

_REQUESTID_EXCLUDED_PATHS: tuple[str, ...] = ("/static/",)
_ACCESSLOG_EXCLUDED_PATHS: tuple[str, ...] = (
    "/metrics",
    "/healthz",
    "/static/",
    "/admin/",
)

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_bot_id_var: ContextVar[str | None] = ContextVar("bot_id", default=None)
_doc_id_var: ContextVar[str | None] = ContextVar("doc_id", default=None)


def set_request_context(
    *,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    doc_id: str | None = None,
) -> None:
    """Populate the per-request ContextVars; called by views after path binding."""
    if tenant_id is not None:
        _tenant_id_var.set(tenant_id)
    if bot_id is not None:
        _bot_id_var.set(bot_id)
    if doc_id is not None:
        _doc_id_var.set(doc_id)


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(_REQUESTID_EXCLUDED_PATHS):
            return self.get_response(request)

        incoming = (request.headers.get("X-Request-ID") or "").strip()[:_MAX_REQUEST_ID_LEN]
        rid = incoming or str(uuid.uuid4())

        rid_token = _request_id_var.set(rid)
        tenant_token = _tenant_id_var.set(None)
        bot_token = _bot_id_var.set(None)
        doc_token = _doc_id_var.set(None)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = rid
            return response
        finally:
            _request_id_var.reset(rid_token)
            _tenant_id_var.reset(tenant_token)
            _bot_id_var.reset(bot_token)
            _doc_id_var.reset(doc_token)


class AccessLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(_ACCESSLOG_EXCLUDED_PATHS):
            return self.get_response(request)

        phases = reset_phase_durations()
        started = time.monotonic()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = (time.monotonic() - started) * 1000.0
            duration_seconds = duration_ms / 1000.0
            extra: dict = {
                "method": request.method,
                "path": request.path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "phases": phases,
            }
            tid = _tenant_id_var.get()
            bid = _bot_id_var.get()
            did = _doc_id_var.get()
            if tid:
                extra["tenant_id"] = tid
            if bid:
                extra["bot_id"] = bid
            if did:
                extra["doc_id"] = did
            logger.info("request_completed", extra=extra)

            # Counter recording — bounded label cardinality via url_name (NOT request.path).
            resolver_match = getattr(request, "resolver_match", None)
            endpoint = (
                getattr(resolver_match, "url_name", None) or "unknown"
                if resolver_match is not None
                else "unknown"
            )
            try:
                record_http_request(
                    method=request.method,
                    endpoint=endpoint,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
                for phase, ms in phases.items():
                    record_pipeline_phase(phase=phase, duration_seconds=ms / 1000.0)
            except Exception:
                # Never let metric recording break a request.
                logger.exception("metrics_record_failed")
