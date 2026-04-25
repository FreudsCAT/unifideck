"""event_bus/supervision/watchdog_handler.py — Timeout + quarantine for handlers.

# OP-10a | event_bus/supervision/watchdog_handler.py | Depends: (none)

Wraps each handler invocation in ``asyncio.wait_for`` with a
per-handler timeout. Tracks consecutive timeouts and quarantines
a handler after N in a row (one success resets the counter).
Quarantined handlers are skipped until ``release_quarantine()``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

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
        self._default_timeout = default_timeout
        self._quarantine_threshold = quarantine_threshold
        self._metrics: dict[str, HandlerTimeoutMetrics] = {}
        self._timeouts: dict[str, float] = {}

    def register(
        self, handler_name: str, timeout: float | None = None,
    ) -> None:
        """Declare a handler + optional custom timeout.
        Idempotent: most recent ``timeout`` wins on re-registration.
        """
        if handler_name not in self._metrics:
            self._metrics[handler_name] = HandlerTimeoutMetrics(name=handler_name)
        if timeout is not None:
            self._timeouts[handler_name] = timeout

    def unregister(self, handler_name: str) -> None:
        """Drop timeout override + reset quarantine state.
        Metrics entry is kept for inspection; a re-subscribed
        handler starts with a clean consecutive counter.
        """
        self._timeouts.pop(handler_name, None)
        metrics = self._metrics.get(handler_name)
        if metrics is not None:
            metrics.consecutive_timeouts = 0
            metrics.quarantined = False

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
        if handler_kwargs is None:
            handler_kwargs = {}

        # Auto-register if not known
        if handler_name not in self._metrics:
            self.register(handler_name)

        metrics = self._metrics[handler_name]

        # Check quarantine
        if metrics.quarantined:
            raise HandlerQuarantinedError(handler_name)

        timeout = self._timeouts.get(handler_name, self._default_timeout)
        metrics.invocations += 1

        try:
            coro = handler(*handler_args, **handler_kwargs)
            result = await asyncio.wait_for(coro, timeout=timeout)
            # Success — reset consecutive counter
            metrics.consecutive_timeouts = 0
            return result
        except asyncio.TimeoutError:
            self._record_timeout(metrics, timeout)
            raise

    def release_quarantine(self, handler_name: str) -> bool:
        """Manually clear quarantine after deploying a fix.
        Returns True if it was quarantined and is now released,
        False if it wasn't quarantined.
        """
        metrics = self._metrics.get(handler_name)
        if metrics is None or not metrics.quarantined:
            return False
        metrics.quarantined = False
        metrics.consecutive_timeouts = 0
        logger.info("[Watchdog] Released quarantine for %s", handler_name)
        return True

    def quarantine_preemptive(
        self, handler_name: str, reason: str = "preemptive",
    ) -> bool:
        """Mark a handler quarantined without waiting for failures.
        Used by ops tooling to pull a bad handler out of rotation
        before it accumulates enough timeouts.
        """
        if handler_name not in self._metrics:
            self.register(handler_name)
        metrics = self._metrics[handler_name]
        if metrics.quarantined:
            return False
        metrics.quarantined = True
        metrics.last_error = reason
        logger.error("[Watchdog] Preemptive quarantine for %s: %s", handler_name, reason)
        return True

    def get_metrics(self) -> dict[str, HandlerTimeoutMetrics]:
        """Return a snapshot copy of all tracked handlers."""
        return dict(self._metrics)

    def _record_timeout(
        self,
        metrics: HandlerTimeoutMetrics,
        timeout: float,
    ) -> None:
        """Update ``timeouts`` + ``consecutive_timeouts`` on timeout.
        When ``consecutive_timeouts >= quarantine_threshold``,
        flip ``quarantined = True`` and log ERROR.
        """
        metrics.timeouts += 1
        metrics.consecutive_timeouts += 1
        metrics.last_error = f"timeout after {timeout}s"

        if metrics.consecutive_timeouts >= self._quarantine_threshold:
            metrics.quarantined = True
            logger.error(
                "[Watchdog] Quarantined %s after %d consecutive timeouts",
                metrics.name, metrics.consecutive_timeouts,
            )


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
