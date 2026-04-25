"""event_bus/event_bus_reliability.py — Circuit breaker for event handlers.

# OP-09f | event_bus/event_bus_reliability.py | Depends: (none)
"""
from __future__ import annotations

import time
from collections import deque
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failures exceeded threshold — skip handler
    HALF_OPEN = "half_open" # One probe allowed to test recovery


class CircuitBreaker:
    """Open/half-open/closed circuit per handler. Prevents cascade failures."""

    def __init__(self, failure_threshold: int = 3, window_seconds: float = 600) -> None:
        self._threshold = failure_threshold
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._states: dict[str, CircuitState] = {}
        self._last_open: dict[str, float] = {}

    def record_success(self, handler_name: str) -> None:
        """Reset circuit to CLOSED on any success."""
        self._states[handler_name] = CircuitState.CLOSED
        if handler_name in self._failures:
            self._failures[handler_name].clear()

    def record_failure(self, handler_name: str) -> None:
        """Record a failure. Opens circuit if threshold exceeded within window."""
        now = time.monotonic()
        if handler_name not in self._failures:
            self._failures[handler_name] = deque()

        dq = self._failures[handler_name]
        dq.append(now)

        # Prune old failures outside window
        cutoff = now - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= self._threshold:
            self._states[handler_name] = CircuitState.OPEN
            self._last_open[handler_name] = now

    def is_open(self, handler_name: str) -> bool:
        """Return True if handler's circuit is OPEN (should be skipped)."""
        state = self._states.get(handler_name, CircuitState.CLOSED)
        if state == CircuitState.OPEN:
            # Check if enough time has passed to try half-open
            opened_at = self._last_open.get(handler_name, 0)
            if time.monotonic() - opened_at > self._window:
                self._states[handler_name] = CircuitState.HALF_OPEN
                return False  # Allow one probe
            return True
        return False

    def get_state(self, handler_name: str) -> CircuitState:
        return self._states.get(handler_name, CircuitState.CLOSED)
