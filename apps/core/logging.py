"""Structured logging for qdrant_rag.

JSON in production (DEBUG=False), key-value console in dev.
Wires Django, DRF, and gunicorn stdlib loggers through the same processor pipeline
so a single stream of structured records covers the whole service.
"""

from __future__ import annotations

import logging
import logging.config
from typing import Any

import structlog


def _request_context_processor(logger, name, event_dict):
    """Enrich every log event with request_id/tenant_id/bot_id/doc_id from ContextVars.

    Lazy-imports the middleware module to avoid a circular dependency during
    Django settings load (logging.py is imported very early; middleware.py
    imports apps.core.timing which is fine but the middleware module also
    runs Django attribute lookups at import time in some test paths).
    """
    from apps.core.middleware import (
        _bot_id_var,
        _doc_id_var,
        _request_id_var,
        _tenant_id_var,
    )

    for key, var in (
        ("request_id", _request_id_var),
        ("tenant_id", _tenant_id_var),
        ("bot_id", _bot_id_var),
        ("doc_id", _doc_id_var),
    ):
        val = var.get()
        if val is not None:
            event_dict.setdefault(key, val)
    return event_dict


_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.ExtraAdder(),
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    _request_context_processor,
]


def _select_renderer(*, debug: bool) -> Any:
    if debug:
        return structlog.dev.ConsoleRenderer(colors=False)
    return structlog.processors.JSONRenderer()


def configure_logging(*, debug: bool, log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging.

    Idempotent: safe to call multiple times (e.g. from settings + tests).
    Sets a root handler routed through structlog.stdlib.ProcessorFormatter
    so loggers from Django, DRF, gunicorn, and ad-hoc logging.getLogger calls
    flow through the same renderer.
    """
    renderer = _select_renderer(debug=debug)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.ExtraAdder(),
            structlog.stdlib.add_logger_name,
            timestamper,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            _request_context_processor,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structlog": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processor": renderer,
                    "foreign_pre_chain": _SHARED_PROCESSORS,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "structlog",
                },
            },
            "loggers": {
                "": {
                    "handlers": ["default"],
                    "level": log_level,
                    "propagate": False,
                },
                "django": {
                    "handlers": ["default"],
                    "level": log_level,
                    "propagate": False,
                },
                "django.request": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "django.server": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "django.db.backends": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "gunicorn.error": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
                "gunicorn.access": {
                    "handlers": ["default"],
                    "level": "INFO",
                    "propagate": False,
                },
            },
        }
    )

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service="qdrant_rag",
        version="0.1.0-dev",
    )
