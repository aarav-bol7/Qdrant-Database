"""Phase 8a graceful shutdown handler tests.

Default mode: register-the-handler tests via mocks (always run).
Opt-in mode: real-subprocess SIGTERM gated on RUN_SIGTERM_TEST=1.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest


class TestSignalHandlerRegistration:
    def test_serve_registers_sigterm_handler(self):
        """`serve()` calls signal.signal(SIGTERM, ...) before wait_for_termination."""
        from apps.grpc_service import server as server_mod

        recorded: dict[int, callable] = {}

        def fake_signal(sig, handler):
            recorded[sig] = handler
            return None

        # Avoid blocking on wait_for_termination; force grpc.server to a mock.
        fake_grpc_server = MagicMock()
        fake_grpc_server.add_insecure_port.return_value = 50051

        with (
            patch.object(server_mod.signal, "signal", side_effect=fake_signal),
            patch.object(server_mod.grpc, "server", return_value=fake_grpc_server),
            patch.object(server_mod.search_pb2_grpc, "add_VectorSearchServicer_to_server"),
        ):
            # wait_for_termination is the blocking call; replace with a no-op
            fake_grpc_server.wait_for_termination = MagicMock(return_value=None)
            server_mod.serve()

        assert signal.SIGTERM in recorded, f"SIGTERM handler not registered: {list(recorded)}"
        assert callable(recorded[signal.SIGTERM])

    def test_shutdown_handler_calls_server_stop_with_grace(self):
        """Invoking the registered handler triggers server.stop(grace=...)."""
        from apps.grpc_service import server as server_mod

        recorded: dict[int, callable] = {}

        def fake_signal(sig, handler):
            recorded[sig] = handler
            return None

        fake_grpc_server = MagicMock()
        fake_grpc_server.add_insecure_port.return_value = 50051
        # server.stop returns a threading.Event-like; use MagicMock with .wait()
        stop_event = MagicMock()
        fake_grpc_server.stop.return_value = stop_event

        with (
            patch.object(server_mod.signal, "signal", side_effect=fake_signal),
            patch.object(server_mod.grpc, "server", return_value=fake_grpc_server),
            patch.object(server_mod.search_pb2_grpc, "add_VectorSearchServicer_to_server"),
            patch.dict(os.environ, {"GRPC_SHUTDOWN_GRACE_SECONDS": "7"}, clear=False),
        ):
            fake_grpc_server.wait_for_termination = MagicMock(return_value=None)
            server_mod.serve()
            handler = recorded[signal.SIGTERM]
            with pytest.raises(SystemExit) as exc_info:
                handler(signal.SIGTERM, None)
            assert exc_info.value.code == 0

        fake_grpc_server.stop.assert_called_once_with(grace=7)
        stop_event.wait.assert_called_once()

    def test_reflection_off_by_default(self):
        from apps.grpc_service import server as server_mod

        fake_grpc_server = MagicMock()
        fake_grpc_server.add_insecure_port.return_value = 50051
        with (
            patch.object(server_mod.signal, "signal"),
            patch.object(server_mod.grpc, "server", return_value=fake_grpc_server),
            patch.object(server_mod.search_pb2_grpc, "add_VectorSearchServicer_to_server"),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("GRPC_ENABLE_REFLECTION", None)
            fake_grpc_server.wait_for_termination = MagicMock(return_value=None)
            with patch(
                "grpc_reflection.v1alpha.reflection.enable_server_reflection"
            ) as enable_mock:
                server_mod.serve()
                enable_mock.assert_not_called()

    def test_reflection_on_when_env_truthy(self):
        from apps.grpc_service import server as server_mod

        fake_grpc_server = MagicMock()
        fake_grpc_server.add_insecure_port.return_value = 50051
        with (
            patch.object(server_mod.signal, "signal"),
            patch.object(server_mod.grpc, "server", return_value=fake_grpc_server),
            patch.object(server_mod.search_pb2_grpc, "add_VectorSearchServicer_to_server"),
            patch.dict(os.environ, {"GRPC_ENABLE_REFLECTION": "True"}, clear=False),
        ):
            fake_grpc_server.wait_for_termination = MagicMock(return_value=None)
            with patch(
                "grpc_reflection.v1alpha.reflection.enable_server_reflection"
            ) as enable_mock:
                server_mod.serve()
                enable_mock.assert_called_once()


@pytest.mark.skipif(
    os.environ.get("RUN_SIGTERM_TEST") != "1",
    reason="set RUN_SIGTERM_TEST=1 to run real-SIGTERM subprocess test",
)
class TestRealSigtermSubprocess:
    def test_sigterm_drains_within_grace(self, tmp_path):
        env = os.environ.copy()
        env["GRPC_SHUTDOWN_GRACE_SECONDS"] = "5"
        env["GRPC_PORT"] = "50061"  # avoid conflict with running grpc container
        env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        env.setdefault("QDRANT_HOST", "localhost")

        proc = subprocess.Popen(
            [sys.executable, "-m", "apps.grpc_service.server"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            # Give the server time to bind
            time.sleep(2.0)
            proc.send_signal(signal.SIGTERM)
            try:
                rc = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                pytest.fail("server did not exit within 15s of SIGTERM")
            assert rc == 0, f"non-zero exit: {rc}"
        finally:
            if proc.poll() is None:
                proc.kill()
