"""Embedder warmup state + thread starter.

Tests the module-level state, not the celery/Django startup signal — the
signal-connected wrapper is just ``start_warmup_in_background()`` plus the
``IngestionConfig.ready()`` invocation, both exercised separately.
"""
from __future__ import annotations

from unittest import mock

import pytest

from apps.ingestion import _warmup


@pytest.fixture(autouse=True)
def _reset_warmup_state():
    """Reset module-level state before/after every test so they don't leak."""
    _warmup._reset_for_tests()
    yield
    _warmup._reset_for_tests()


def test_is_embedder_loaded_initially_false():
    assert _warmup.is_embedder_loaded() is False


def test_do_warmup_sets_loaded_on_success():
    with mock.patch(
        "apps.ingestion.embedder.embed_query",
        return_value={"dense": object(), "sparse": {}, "colbert": object()},
    ) as m_embed:
        _warmup._do_warmup()
    m_embed.assert_called_once_with("warmup")
    assert _warmup.is_embedder_loaded() is True


def test_do_warmup_failure_keeps_loaded_false():
    # No re-raise — exception is logged and swallowed.
    with mock.patch(
        "apps.ingestion.embedder.embed_query",
        side_effect=RuntimeError("model weights missing"),
    ):
        _warmup._do_warmup()
    assert _warmup.is_embedder_loaded() is False


def test_start_warmup_idempotent():
    """Two calls to start_warmup_in_background must spawn at most one thread."""
    threads_created = []

    real_thread = _warmup.threading.Thread

    def fake_thread_ctor(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        threads_created.append(t)
        return t

    with mock.patch(
        "apps.ingestion.embedder.embed_query",
        return_value={"dense": object(), "sparse": {}, "colbert": object()},
    ), mock.patch.object(_warmup.threading, "Thread", side_effect=fake_thread_ctor):
        _warmup.start_warmup_in_background()
        _warmup.start_warmup_in_background()
        _warmup.start_warmup_in_background()

    assert len(threads_created) == 1
    threads_created[0].join(timeout=5)
    assert _warmup.is_embedder_loaded() is True
