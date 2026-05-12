"""Brute-force detector — slow/lock repeated auth failures.

OP-19c | py_modules/unifideck/services/security/bruteforce.py

``BruteForceDetector`` tracks failed authentication attempts per
(store, identifier) and applies progressive penalties :

* < N failures → no action;
* ≥ N failures → impose an exponentially-growing delay before the
  next attempt is accepted;
* ≥ M failures → temporary lockout (auth refused for the cooldown
  window).

State is in-memory only — a plugin restart resets every counter.
"""

from __future__ import annotations
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class BruteForceDetector:
    """Brute force detector."""

    def __init__(
        self,
        window_seconds: float,
        warning_threshold: int,
        escalation_threshold: int,
        on_threshold_crossed: Callable[..., None],
    ) -> None:
        """Initialize the instance."""
        self._window = window_seconds
        self._warning = warning_threshold
        self._escalation = escalation_threshold
        self._failures: deque[float] = deque(maxlen=escalation_threshold * 2)
        self._escalated = False
        self._on_crossed = on_threshold_crossed

    def check(self) -> None:
        """Check."""
        now = time.monotonic()
        self._failures.append(now)
        recent = sum(1 for ts in self._failures if now - ts <= self._window)
        if recent >= self._escalation and not self._escalated:
            self._escalated = True
            logger.error(
                "[BruteForceDetector] ESCALATION: %d failures in %.0fs",
                recent,
                self._window,
            )
            self._on_crossed(level="escalation", recent_failures=recent)
        elif recent >= self._warning:
            logger.warning(
                "[BruteForceDetector] warning: %d failures in %.0fs",
                recent,
                self._window,
            )
            self._on_crossed(level="warning", recent_failures=recent)

    def status(self) -> dict[str, Any]:
        """Status."""
        now = time.monotonic()
        recent = sum(1 for ts in self._failures if now - ts <= self._window)
        return {
            "recent_failures": recent,
            "window_seconds": self._window,
            "warning_threshold": self._warning,
            "escalation_threshold": self._escalation,
            "escalated": self._escalated,
        }

    def reset(self) -> None:
        """Reset."""
        self._failures.clear()
        self._escalated = False
