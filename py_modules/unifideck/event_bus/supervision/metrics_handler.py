"""event_bus/supervision/metrics_handler.py — Per-handler latency metrics.

Design:
  - Each handler has a rolling window of the last 100 measurements
    (deque with maxlen). Oldest entries drop off naturally.
  - Percentiles (p50, p95) are computed on-demand via
    `statistics.quantiles()` from the rolling window.
  - Lifetime counters (invocations, total_ms, max_ms) are kept
    separately from the window for long-term trends.
  - Memory bounded: 100 floats per handler × ~24 handlers = ~20 KB
    total. Safe to keep running for days.

Why a separate module from watchdog_handler:
  - SRP: watchdog = reliability, metrics = observability. Two
    modules mean each can evolve independently.
  - The metrics collector has no failure modes — it just records
    numbers. It doesn't need the exception handling and state
    management of the watchdog.

"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Size of the rolling window per handler. 100 samples is enough
# for a stable p95 while keeping memory bounded. At ~8 bytes per
# float, that's 800 bytes per handler — negligible.
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
    # The rolling window itself. Not serialized in to_dict()
    # because it's an implementation detail.
    _window: deque[float] = field(
        default_factory=lambda: deque(maxlen=ROLLING_WINDOW_SIZE),
    )

    def record(self, duration_ms: float) -> None:
        """Append a new measurement and update aggregates."""
        self.invocations += 1
        self.total_ms += duration_ms
        if duration_ms > self.max_ms:
            self.max_ms = duration_ms
        self._window.append(duration_ms)
        self._recompute_percentiles()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot for the RPC response.

        Excludes the internal deque. Callers that want the raw
        window can access `_window` directly (not recommended for
        stable APIs).
        """
        avg = self.total_ms / self.invocations if self.invocations else 0.0
        return {
            "name": self.name,
            "invocations": self.invocations,
            "total_ms": round(self.total_ms, 2),
            "avg_ms": round(avg, 2),
            "max_ms": round(self.max_ms, 2),
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
        }

    # ── Private ──────────────────────────────────────────────────

    def _recompute_percentiles(self) -> None:
        """Recompute p50/p95 from the rolling window.

        `statistics.quantiles(n=20)` returns the 19 cut-points,
        giving p5, p10, ..., p95 at index 18. p50 is index 9.
        Needs at least 2 data points — for 1 sample we just use
        that value.
        """
        n = len(self._window)
        if n == 0:
            return
        if n == 1:
            self.p50_ms = self.p95_ms = self._window[0]
            return
        qs = statistics.quantiles(self._window, n=20)
        self.p50_ms = qs[9]
        self.p95_ms = qs[18]


class HandlerLatencyCollector:
    """Central registry of per-handler latency stats.

    Usage:
        collector = HandlerLatencyCollector()
        t0 = time.monotonic()
        await handler(...)
        collector.record("my.handler", (time.monotonic() - t0) * 1000)
        snapshot = collector.get_snapshot()  # for RPC response
    """

    def __init__(self) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._stats: dict[str, HandlerLatencyStats] = {}

    def record(self, handler_name: str, duration_ms: float) -> None:
        """Record one invocation's duration. Cheap — O(log n)."""
        stats = self._stats.get(handler_name)
        if stats is None:
            stats = HandlerLatencyStats(name=handler_name)
            self._stats[handler_name] = stats
        stats.record(duration_ms)

    def get_snapshot(self) -> dict[str, dict[str, float]]:
        """Return all handler stats as a JSON-serializable dict."""
        return {
            name: stats.to_dict() for name, stats in self._stats.items()
        }

    def get_top_n(self, n: int = 10) -> dict[str, dict[str, float]]:
        """Return the top-N slowest handlers by p95 latency.

        Useful for frontend dashboards that want to show "which
        handlers to look at first" without rendering all 24.
        """
        sorted_stats = sorted(
            self._stats.values(),
            key=lambda s: s.p95_ms,
            reverse=True,
        )
        return {s.name: s.to_dict() for s in sorted_stats[:n]}

    def reset(self, handler_name: str) -> bool:
        """Clear stats for one handler. Returns True if it existed."""
        if handler_name in self._stats:
            self._stats[handler_name] = HandlerLatencyStats(
                name=handler_name,
            )
            return True
        return False
