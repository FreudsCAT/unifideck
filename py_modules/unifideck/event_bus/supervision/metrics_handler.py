"""event_bus/supervision/metrics_handler.py — Per-handler latency metrics.

# OP-10b | event_bus/supervision/metrics_handler.py | Depends: (none)

Rolling window of the last 100 measurements per handler. p50/p95
computed on-demand via ``statistics.quantiles``. Lifetime counters
(invocations, total_ms, max_ms) kept separately for long-term
trends. ~20 KB total — safe to run for days.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

ROLLING_WINDOW_SIZE = 100


@dataclass
class HandlerLatencyStats:
    """Latency statistics for a single handler."""

    name: str
    invocations: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    _window: deque[float] = field(
        default_factory=lambda: deque(maxlen=ROLLING_WINDOW_SIZE),
    )

    def record(self, duration_ms: float) -> None:
        """Append a measurement, update counters + max, recompute
        percentiles from the current window.
        """
        raise NotImplementedError("OP-10b: append to _window, update counters, call _recompute")

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable snapshot (excludes internal deque).
        Includes computed ``avg_ms = total_ms / invocations`` rounded
        to 2 decimals; zero when no invocations yet.
        """
        raise NotImplementedError("OP-10b: build dict without _window field")

    def _recompute_percentiles(self) -> None:
        """Update ``p50_ms`` / ``p95_ms`` from the rolling window.
        Uses ``statistics.quantiles(window, n=20)`` — index 9 is p50,
        index 18 is p95. Degenerate cases: empty window → no-op,
        single sample → both percentiles equal that value.
        """
        raise NotImplementedError("OP-10b: statistics.quantiles or single-sample fallback")


class HandlerLatencyCollector:
    """Central registry of per-handler latency stats."""

    def __init__(self) -> None:
        """Init empty ``{handler_name: HandlerLatencyStats}`` dict."""
        raise NotImplementedError("OP-10b: self._stats: dict = {}")

    def record(self, handler_name: str, duration_ms: float) -> None:
        """Record one invocation's duration. O(log n) from quantiles.
        Lazily creates the stats entry on first call per handler.
        """
        raise NotImplementedError("OP-10b: get or create stats entry, call record()")

    def get_snapshot(self) -> dict[str, dict[str, float]]:
        """Return all handler stats as ``{name: stats_dict}``."""
        raise NotImplementedError("OP-10b: {k: v.to_dict() for k,v in self._stats.items()}")

    def get_top_n(self, n: int = 10) -> dict[str, dict[str, float]]:
        """Return the top-N slowest handlers ranked by p95 latency.
        Useful for dashboards that want "which handlers to look at
        first" without rendering the full list.
        """
        raise NotImplementedError("OP-10b: sort by p95_ms, return top n as dict")

    def reset(self, handler_name: str) -> bool:
        """Clear stats for one handler. Return True if it existed."""
        raise NotImplementedError("OP-10b: pop handler_name from _stats")
