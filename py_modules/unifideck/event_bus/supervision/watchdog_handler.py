"""event_bus/supervision/watchdog_handler.py — Timeout + quarantine for handlers.

# OP-10a | event_bus/supervision/watchdog_handler.py | Depends: (none)

Wraps each handler invocation in ``asyncio.wait_for`` with a
per-handler timeout. Tracks consecutive timeouts and quarantines
a handler after N in a row (one success resets the counter).
Quarantined handlers are skipped until ``release_quarantine()``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_HANDLER_TIMEOUT_SEC = 5.0
DEFAULT_QUARANTINE_THRESHOLD = 10


@dataclass
class HandlerTimeoutMetrics:
    """Per-handler timing + health state, surfaced via ``get_metrics()``
    and merged into the plugin-level ``get_bus_health()`` RPC.
    """
    name: str
    invocations: int = 0
    timeouts: int = 0
    consecutive_timeouts: int = 0
    quarantined: bool = False
    last_error: str | None = None


class HandlerWatchdog:
    """Per-handler timeout enforcement + quarantine bookkeeping.

    Single instance per ``PriorityDispatcher``. Does not own the
    handler registry — just tracks health keyed by a stable handler
    identifier (usually the function's ``__qualname__``).
    """

    def __init__(
        self,
        *,
        default_timeout: float = DEFAULT_HANDLER_TIMEOUT_SEC,
        quarantine_threshold: int = DEFAULT_QUARANTINE_THRESHOLD,
    ) -> None:
        """Init thresholds, empty ``{handler_name: HandlerTimeoutMetrics}``
        and ``{handler_name: timeout_override}`` dicts.
        """
        raise NotImplementedError("OP-10a: init dicts and store thresholds")

    def register(
        self, handler_name: str, timeout: float | None = None,
    ) -> None:
        """Declare a handler + optional custom timeout.
        Idempotent: most recent ``timeout`` wins on re-registration.
        """
        raise NotImplementedError("OP-10a: upsert metrics entry, store timeout override")

    def unregister(self, handler_name: str) -> None:
        """Drop timeout override + reset quarantine state.
        Metrics entry is kept for inspection; a re-subscribed
        handler starts with a clean consecutive counter.
        """
        raise NotImplementedError("OP-10a: pop timeout override, reset consecutive counter")

    async def invoke(
        self,
        *,
        handler_name: str,
        handler: Callable[..., Any],
        handler_args: tuple = (),
        handler_kwargs: dict | None = None,
    ) -> Any:
        """Run ``handler`` under the watchdog timeout.
        Raises ``HandlerQuarantinedError`` if quarantined, re-raises
        ``TimeoutError`` on timeout (after counter update), and
        propagates any handler exception unchanged (not counted).
        On success, resets ``consecutive_timeouts`` to 0.
        """
        raise NotImplementedError("OP-10a: asyncio.wait_for + quarantine + counter logic")

    def release_quarantine(self, handler_name: str) -> bool:
        """Manually clear quarantine after deploying a fix.
        Returns True if it was quarantined and is now released,
        False if it wasn't quarantined.
        """
        raise NotImplementedError("OP-10a: flip quarantined flag if set")

    def quarantine_preemptive(
        self, handler_name: str, reason: str = "preemptive",
    ) -> bool:
        """Mark a handler quarantined without waiting for failures.
        Used by ops tooling to pull a bad handler out of rotation
        before it accumulates enough timeouts.
        """
        raise NotImplementedError("OP-10a: set quarantined=True, log reason")

    def get_metrics(self) -> dict[str, HandlerTimeoutMetrics]:
        """Return a snapshot copy of all tracked handlers."""
        raise NotImplementedError("OP-10a: return dict copy of metrics")

    def _record_timeout(
        self,
        metrics: HandlerTimeoutMetrics,
        timeout: float,
    ) -> None:
        """Update ``timeouts`` + ``consecutive_timeouts`` on timeout.
        When ``consecutive_timeouts >= quarantine_threshold``,
        flip ``quarantined = True`` and log ERROR.
        """
        raise NotImplementedError("OP-10a: increment counters, check threshold")


class HandlerQuarantinedError(Exception):
    """Raised by ``invoke()`` when the handler is in quarantine.
    Distinct from generic exceptions so callers can catch it
    specifically and skip silently — the ERROR was already logged
    when quarantine was triggered.
    """

    def __init__(self, handler_name: str) -> None:
        """Store ``handler_name`` on the instance + set message."""
        super().__init__(f"Handler '{handler_name}' is quarantined")
        self.handler_name = handler_name
