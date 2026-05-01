"""event_bus/supervision/watchdog_handler.py — Timeout + quarantine for EventBus handlers.

This module provides:

  1. `HandlerWatchdog.invoke(handler, *args, **kwargs)` — a wrapper
     that runs a handler with `asyncio.wait_for()` and a per-handler
     timeout. On timeout, it raises `asyncio.TimeoutError` up to
     the caller (which logs and continues with the next event).

  2. A `consecutive_timeouts` counter per handler. When a handler
     accumulates N consecutive timeouts (default 10), it is marked
     as `quarantined` — future invocations return immediately
     without calling the handler. A log ERROR is emitted on
     quarantine entry, and an RPC `release_quarantine(handler)`
     lets operators un-quarantine it after a fix.

  3. A `HandlerTimeoutMetrics` dataclass exposed via
     `get_metrics()` for integration into `get_bus_health()`.

Design principles:
  - **Occasional timeouts are normal**. A single slow network call
    is not a reason to ban a handler. Quarantine only triggers on
    N *consecutive* failures — one successful invocation resets
    the counter to 0.
  - **No data loss on quarantine**. The event that tripped the
    quarantine is still dispatched to the other (healthy) handlers.
    Only the broken handler is bypassed.
  - **No silent failures**. Every timeout logs WARNING. Quarantine
    entry logs ERROR. Release logs INFO.
  - **Thread-safety not required**. Runs on the single asyncio
    loop like the rest of the dispatcher — no locks needed.

"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Default timeout when a handler is registered without an explicit
# value. 5 seconds is generous — anything longer on the Steam Deck
# main loop is almost certainly a bug. Operators can override per
# handler via `EventBus.on(event, handler, timeout=...)`.
DEFAULT_HANDLER_TIMEOUT_SEC = 5.0

# Consecutive-timeout threshold for auto-quarantine. Ten in a row
# means either the handler itself is broken (quarantine is correct)
# or the remote service it talks to is down (quarantine is still
# correct — we don't want to flood the dispatcher with timeouts).
DEFAULT_QUARANTINE_THRESHOLD = 10


@dataclass
class HandlerTimeoutMetrics:
    """Per-handler timing and health state for the watchdog.

    Exposed via `HandlerWatchdog.get_metrics()` and merged into the
    plugin-level `get_bus_health()` RPC response so operators can
    see which handlers are flaky from the frontend diagnostics tab.
    """

    name: str
    invocations: int = 0
    timeouts: int = 0
    consecutive_timeouts: int = 0
    quarantined: bool = False
    last_error: str | None = None


class HandlerWatchdog:
    """Per-handler timeout enforcement + quarantine bookkeeping.

    Single-instance per PriorityDispatcher. Does NOT own the handler
    registry — the EventBus still owns the (event → handlers) map.
    This class just tracks health state keyed by a stable handler
    identifier (usually the function's `__qualname__`).
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        *,
        default_timeout: float = DEFAULT_HANDLER_TIMEOUT_SEC,
        quarantine_threshold: int = DEFAULT_QUARANTINE_THRESHOLD,
    ) -> None:
        self._default_timeout = default_timeout
        self._quarantine_threshold = quarantine_threshold
        self._metrics: dict[str, HandlerTimeoutMetrics] = {}
        # Per-handler timeout override, set at subscribe time.
        self._timeouts: dict[str, float] = {}

    # ── Registration API ────────────────────────────────────────

    def register(
        self,
        handler_name: str,
        timeout: float | None = None,
    ) -> None:
        """Declare a handler and its optional custom timeout.

        Called from `EventBus.on()` or `EventBus.once()`. Safe to
        call multiple times for the same handler — the most recent
        timeout wins.
        """
        if timeout is not None:
            self._timeouts[handler_name] = timeout
        if handler_name not in self._metrics:
            self._metrics[handler_name] = HandlerTimeoutMetrics(
                name=handler_name,
            )

    def unregister(self, handler_name: str) -> None:
        """Drop the handler from watchdog bookkeeping.

        Called when `EventBus.off()` removes a subscription. The
        metrics entry is kept for inspection but the quarantine
        state is cleared so a re-subscribed handler starts fresh.
        """
        self._timeouts.pop(handler_name, None)
        m = self._metrics.get(handler_name)
        if m is not None:
            m.quarantined = False
            m.consecutive_timeouts = 0

    # ── Invocation API ──────────────────────────────────────────

    async def invoke(
        self,
        handler_name: str,
        handler: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run a handler with the watchdog timeout.

        Returns the handler's return value on success. Raises:
          - `HandlerQuarantinedError` if the handler is currently
            quarantined (caller should just skip it).
          - `asyncio.TimeoutError` on timeout (metric updated,
            caller should log and continue).
          - Any exception raised by the handler itself (propagated
            unchanged — not counted as a timeout).
        """
        metrics = self._metrics.setdefault(
            handler_name, HandlerTimeoutMetrics(name=handler_name),
        )
        if metrics.quarantined:
            raise HandlerQuarantinedError(handler_name)

        timeout = self._timeouts.get(handler_name, self._default_timeout)
        metrics.invocations += 1
        try:
            result = await asyncio.wait_for(
                handler(*args, **kwargs),
                timeout=timeout,
            )
            # Success: reset the consecutive counter
            metrics.consecutive_timeouts = 0
            metrics.last_error = None
            return result
        except TimeoutError:
            self._record_timeout(metrics, timeout)
            raise

    # ── Operator API ────────────────────────────────────────────

    def release_quarantine(self, handler_name: str) -> bool:
        """Manually release a handler from quarantine.

        Returns True if the handler was quarantined and is now
        cleared, False if it was not quarantined to begin with.
        Typically called after deploying a fix — the operator
        doesn't want to wait for a full plugin reload.
        """
        m = self._metrics.get(handler_name)
        if m is None or not m.quarantined:
            return False
        m.quarantined = False
        m.consecutive_timeouts = 0
        logger.info(
            "[HandlerWatchdog] released %s from quarantine",
            handler_name,
        )
        return True

    def quarantine_preemptive(
        self, handler_name: str, reason: str = "preemptive",
    ) -> bool:
        """Mark a handler as quarantined without waiting for failures."""
        metrics = self._metrics.setdefault(
            handler_name, HandlerTimeoutMetrics(name=handler_name),
        )
        if metrics.quarantined:
            return False
        metrics.quarantined = True
        metrics.last_error = f"quarantined preemptively: {reason}"
        logger.warning(
            "[HandlerWatchdog] %s quarantined preemptively (%s)",
            handler_name, reason,
        )
        return True

    def get_metrics(self) -> dict[str, HandlerTimeoutMetrics]:
        """Return a snapshot of all tracked handlers."""
        return dict(self._metrics)

    # ── Private helpers ─────────────────────────────────────────

    def _record_timeout(
        self,
        metrics: HandlerTimeoutMetrics,
        timeout: float,
    ) -> None:
        """Update counters on timeout and quarantine if needed."""
        metrics.timeouts += 1
        metrics.consecutive_timeouts += 1
        metrics.last_error = f"timeout after {timeout:.1f}s"
        logger.warning(
            "[HandlerWatchdog] %s timed out (%d/%d consecutive)",
            metrics.name,
            metrics.consecutive_timeouts,
            self._quarantine_threshold,
        )
        if metrics.consecutive_timeouts >= self._quarantine_threshold:
            metrics.quarantined = True
            logger.error(
                "[HandlerWatchdog] QUARANTINED %s after %d "
                "consecutive timeouts — will be skipped until "
                "release_quarantine() is called",
                metrics.name,
                metrics.consecutive_timeouts,
            )


class HandlerQuarantinedError(Exception):
    """Raised by `invoke()` when the handler is in quarantine.

    Distinct from generic exceptions so callers can catch it
    specifically and skip the handler without logging it as an
    error — the ERROR was already logged when quarantine was
    triggered.
    """

    def __init__(self, handler_name: str) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        super().__init__(f"handler {handler_name} is quarantined")
        self.handler_name = handler_name
