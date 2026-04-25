"""event_bus/supervision/metrics_handler.py — Per-handler latency metrics.

# OP-10b | event_bus/supervision/metrics_handler.py | Depends: (none)

Rolling window of the last 100 measurements per handler. p50/p95
computed on-demand via ``statistics.quantiles``. Lifetime counters
(invocations, total_ms, max_ms) kept separately for long-term
trends. ~20 KB total — safe to run for days.
"""
from __future__ import annotations

import statistics
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
        self.invocations += 1
        self.total_ms += duration_ms
        if duration_ms > self.max_ms:
            self.max_ms = duration_ms
        self._window.append(duration_ms)
        self._recompute_percentiles()

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable snapshot (excludes internal deque).
        Includes computed ``avg_ms = total_ms / invocations`` rounded
        to 2 decimals; zero when no invocations yet.
        """
        avg_ms = round(self.total_ms / self.invocations, 2) if self.invocations else 0.0
        return {
            "name": self.name,
            "invocations": self.invocations,
            "total_ms": round(self.total_ms, 2),
            "avg_ms": avg_ms,
            "max_ms": round(self.max_ms, 2),
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
        }

    def _recompute_percentiles(self) -> None:
        """Update ``p50_ms`` / ``p95_ms`` from the rolling window.
        Uses ``statistics.quantiles(window, n=20)`` — index 9 is p50,
        index 18 is p95. Degenerate cases: empty window → no-op,
        single sample → both percentiles equal that value.
        """
        n = len(self._window)
        if n == 0:
            return
        if n == 1:
            self.p50_ms = self._window[0]
            self.p95_ms = self._window[0]
            return
        sorted_window = sorted(self._window)
        try:
            q = statistics.quantiles(sorted_window, n=20)
            self.p50_ms = q[9]   # 50th percentile
            self.p95_ms = q[18]  # 95th percentile
        except statistics.StatisticsError:
            # Fewer than 2 data points for quantiles
            self.p50_ms = sorted_window[n // 2]
            self.p95_ms = sorted_window[-1]


class HandlerLatencyCollector:
    """Central registry of per-handler latency stats."""

    def __init__(self) -> None:
        """Init empty ``{handler_name: HandlerLatencyStats}`` dict."""
        self._stats: dict[str, HandlerLatencyStats] = {}

    def record(self, handler_name: str, duration_ms: float) -> None:
        """Record one invocation's duration. O(log n) from quantiles.
        Lazily creates the stats entry on first call per handler.
        """
        stats = self._stats.get(handler_name)
        if stats is None:
            stats = HandlerLatencyStats(name=handler_name)
            self._stats[handler_name] = stats
        stats.record(duration_ms)

    def get_snapshot(self) -> dict[str, dict[str, float]]:
        """Return all handler stats as ``{name: stats_dict}``."""
        return {name: stats.to_dict() for name, stats in self._stats.items()}

    def get_top_n(self, n: int = 10) -> dict[str, dict[str, float]]:
        """Return the top-N slowest handlers ranked by p95 latency.
        Useful for dashboards that want "which handlers to look at
        first" without rendering the full list.
        """
        ranked = sorted(
            self._stats.items(),
            key=lambda kv: kv[1].p95_ms,
            reverse=True,
        )
        return {name: stats.to_dict() for name, stats in ranked[:n]}

    def reset(self, handler_name: str) -> bool:
        """Clear stats for one handler. Return True if it existed."""
        return self._stats.pop(handler_name, None) is not None
