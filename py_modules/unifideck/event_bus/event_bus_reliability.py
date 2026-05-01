"""event_bus/event_bus_reliability.py — Circuit breaker for handlers.

CircuitBreaker trips per handler when the failure rate exceeds a
threshold over a sliding window, complementing the consecutive-
timeout quarantine from watchdog_handler.py.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


CB_WINDOW_SIZE = 20
CB_OPEN_THRESHOLD = 0.5
CB_RESET_TIMEOUT_SEC = 30.0

@dataclass
class _CBState:
    window: deque[bool] = field(
        default_factory=lambda: deque(maxlen=CB_WINDOW_SIZE),
    )
    open_until: float = 0.0  # monotonic, 0 = closed


class CircuitBreaker:
    """Per-handler open/closed state based on failure rate.

    Unlike the watchdog's consecutive-timeout quarantine, the
    circuit breaker trips on **rate** over a sliding window. A
    handler that fails 50% of the time is clearly broken even
    if it never has 10 consecutive failures.

    States:
      - closed: all calls pass through; failures tracked.
      - open: calls rejected immediately for `reset_timeout`
        seconds, after which one probe is allowed.
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        *,
        open_threshold: float = CB_OPEN_THRESHOLD,
        reset_timeout: float = CB_RESET_TIMEOUT_SEC,
    ) -> None:
        self._open_threshold = open_threshold
        self._reset_timeout = reset_timeout
        self._state: dict[str, _CBState] = {}

    def allow(self, handler_name: str) -> bool:
        """Return True if the call should proceed."""
        s = self._state.get(handler_name)
        if s is None or s.open_until == 0.0:
            return True
        if time.monotonic() >= s.open_until:
            # Half-open probe: one call allowed
            s.open_until = 0.0
            return True
        return False

    def record(self, handler_name: str, success: bool) -> None:
        """Record outcome. Trips the breaker if rate exceeds."""
        s = self._state.setdefault(handler_name, _CBState())
        s.window.append(success)
        if len(s.window) < (s.window.maxlen or 0):
            return
        failures = s.window.count(False)
        rate = failures / len(s.window)
        if rate >= self._open_threshold and s.open_until == 0.0:
            s.open_until = time.monotonic() + self._reset_timeout
            logger.warning(
                "[CircuitBreaker] %s opened (failure rate=%.0f%%)",
                handler_name, rate * 100,
            )

    def is_open(self, handler_name: str) -> bool:  # noqa: D102 — documentation pending (Sprint D)
        s = self._state.get(handler_name)
        return s is not None and s.open_until > time.monotonic()
