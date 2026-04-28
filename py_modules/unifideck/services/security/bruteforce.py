"""services.security.bruteforce — Brute-force detector (sliding window).

Extracted from the flat ``security_service.py`` on 2026-04-18 to
encapsulate the state and thresholds of the brute-force detection
policy (Policy 1).

The detector is **stateful** — it owns:

  - A ring buffer of decrypt failure timestamps.
  - Two thresholds (warning and escalation).
  - A sliding window in seconds.
  - An "escalated" latch that fires the escalation event exactly
    once per burst (not on every subsequent failure).

SecurityService composes a single detector via ``self._bf`` and
calls ``check()`` from its SECURITY_DECRYPT_FAILED handler.
Threshold-crossings are surfaced via a caller-supplied callback
so this module stays independent of the event bus.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class BruteForceDetector:
    """Sliding-window counter with warning + escalation thresholds.

    Two thresholds:

      - ``warning_threshold`` (default 5 failures in 60s): logs a
        warning and fires the callback with ``level="warning"``.
        Can fire repeatedly — each subsequent failure above the
        warning but below escalation re-fires.
      - ``escalation_threshold`` (default 20): fires the callback
        with ``level="escalation"`` exactly once per burst. The
        escalated flag is latched until ``reset()`` is called by
        an operator via the RPC.
    """

    def __init__(
        self,
        window_seconds: float,
        warning_threshold: int,
        escalation_threshold: int,
        on_threshold_crossed: Callable[..., None],
    ) -> None:
        """Initialise the detector.

        Args:
            window_seconds: Sliding window duration in seconds.
            warning_threshold: Failures-in-window count that
                triggers a warning-level notification.
            escalation_threshold: Higher count that triggers a
                one-shot escalation notification.
            on_threshold_crossed: Callback invoked when a
                threshold is crossed. Signature:
                ``on_threshold_crossed(level, recent_failures)``
                where ``level`` is ``"warning"`` or
                ``"escalation"``. Intended to emit the
                SECURITY_BRUTEFORCE_SUSPECTED event.
        """
        self._window = window_seconds
        self._warning = warning_threshold
        self._escalation = escalation_threshold
        # Capacity = 2x escalation so we keep enough headroom to
        # observe recent bursts but don't grow unbounded.
        self._failures: deque[float] = deque(maxlen=escalation_threshold * 2)
        self._escalated = False
        self._on_crossed = on_threshold_crossed

    def check(self) -> None:
        """Record a decrypt failure and check thresholds.

        Called from SecurityService's SECURITY_DECRYPT_FAILED
        handler. Appends the current monotonic time and scans
        the deque for failures within the window. Fires the
        callback if a threshold is crossed.
        """
        now = time.monotonic()
        self._failures.append(now)
        recent = sum(
            1 for ts in self._failures
            if now - ts <= self._window
        )
        if recent >= self._escalation and not self._escalated:
            self._escalated = True
            logger.error(
                "[BruteForceDetector] ESCALATION: %d failures "
                "in %.0fs", recent, self._window,
            )
            self._on_crossed(level="escalation", recent_failures=recent)
        elif recent >= self._warning:
            logger.warning(
                "[BruteForceDetector] warning: %d failures "
                "in %.0fs", recent, self._window,
            )
            self._on_crossed(level="warning", recent_failures=recent)

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the detector state for RPC exposure."""
        now = time.monotonic()
        recent = sum(
            1 for ts in self._failures
            if now - ts <= self._window
        )
        return {
            "recent_failures": recent,
            "window_seconds": self._window,
            "warning_threshold": self._warning,
            "escalation_threshold": self._escalation,
            "escalated": self._escalated,
        }

    def reset(self) -> None:
        """Clear the failure buffer and unlatch the escalation.

        Called by the operator via ``reset_bruteforce_state``
        RPC after reviewing the audit log. Not called by
        ``clear_audit_log`` on purpose: clearing the visible log
        should not also clear the detector state (an attacker
        could otherwise hide their trail by triggering a log
        wipe).
        """
        self._failures.clear()
        self._escalated = False
