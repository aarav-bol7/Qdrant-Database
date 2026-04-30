"""Per-request phase-timing helpers.

Backed by a ContextVar dict that AccessLogMiddleware initializes per request
and reads at request end. Pipeline phases wrap themselves in `with timer(name):`.
No-op if no request scope is active (e.g., management commands, tests without
the middleware).
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Iterator

_phase_durations_var: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "phase_durations",
    default=None,
)


def reset_phase_durations() -> dict[str, float]:
    """Initialize a fresh per-request phase-durations dict and return it."""
    d: dict[str, float] = {}
    _phase_durations_var.set(d)
    return d


def get_phase_durations() -> dict[str, float]:
    """Return the current phase-durations dict (or a fresh empty one if no scope)."""
    d = _phase_durations_var.get()
    return d if d is not None else {}


@contextlib.contextmanager
def timer(phase: str) -> Iterator[None]:
    """Record elapsed milliseconds for `phase` into the per-request dict.

    No-op outside a request scope. Always records (even on exception) via
    try/finally so failed phases still log a duration.
    """
    d = _phase_durations_var.get()
    started = time.monotonic()
    try:
        yield
    finally:
        if d is not None:
            d[phase] = (time.monotonic() - started) * 1000.0
