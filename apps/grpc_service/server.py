"""gRPC server entrypoint.

Run with: uv run python -m apps.grpc_service.server

Phase 8a additions:
- env-flagged reflection (default OFF; production stays slim)
- main-thread SIGTERM handler with configurable graceful-shutdown grace
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from concurrent import futures

import django
import grpc

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.grpc_service.generated import search_pb2, search_pb2_grpc  # noqa: E402
from apps.grpc_service.handler import VectorSearchService  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_PORT = 50051
DEFAULT_MAX_WORKERS = 10
DEFAULT_GRACEFUL_SHUTDOWN_S = 10


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def serve() -> None:
    port = int(os.environ.get("GRPC_PORT", DEFAULT_PORT))
    max_workers = int(os.environ.get("GRPC_MAX_WORKERS", DEFAULT_MAX_WORKERS))
    grace_s = int(os.environ.get("GRPC_SHUTDOWN_GRACE_SECONDS", DEFAULT_GRACEFUL_SHUTDOWN_S))
    enable_reflection = _truthy(os.environ.get("GRPC_ENABLE_REFLECTION"))

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    search_pb2_grpc.add_VectorSearchServicer_to_server(VectorSearchService(), server)

    if enable_reflection:
        try:
            from grpc_reflection.v1alpha import reflection

            service_names = (
                search_pb2.DESCRIPTOR.services_by_name["VectorSearch"].full_name,
                reflection.SERVICE_NAME,
            )
            reflection.enable_server_reflection(service_names, server)
            logger.info("grpc_reflection_enabled", extra={"services": list(service_names)})
        except ImportError:
            logger.warning(
                "grpc_reflection_unavailable",
                extra={"hint": "install grpcio-reflection in dev deps"},
            )

    bind_addr = f"0.0.0.0:{port}"
    bound_port = server.add_insecure_port(bind_addr)
    if not bound_port:
        raise SystemExit(f"grpc_server bind failed: {bind_addr}")

    def _shutdown(signum, frame):
        logger.info(
            "grpc_shutdown_initiated",
            extra={"signal": signum, "grace_s": grace_s},
        )
        stop_event = server.stop(grace=grace_s)
        stop_event.wait()
        logger.info("grpc_shutdown_complete")
        sys.exit(0)

    # Signal handlers must be installed in the main thread; serve() is invoked
    # from `if __name__ == "__main__":` so we are in main here.
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.start()
    logger.info(
        "grpc_server_started",
        extra={
            "port": bound_port,
            "workers": max_workers,
            "grace_s": grace_s,
            "reflection": enable_reflection,
        },
    )
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
