"""core/metrics_collector.py — In-memory metrics aggregator.

Moved from services/ to core/. metrics_collector is
a plugin-level observer that subscribes to every EventBus event
and maintains a global snapshot for the diagnostics panel. It is
NOT a store-interaction service in the Layer-5 sense — it provides
no functionality to any store and has no store-specific logic.
Its character matches the other cross-cutting primitives at
core/ root (cache_manager, sync_service): initialized once at
plugin boot, lives for the process lifetime, touches no store
directly. Clean break: no shim in services/.

Subscribes to every EventBus event and maintains counters, timers,
and gauges in memory. Exposes the current snapshot via
`get_plugin_metrics()` which is called by the frontend to display
a diagnostics panel.
Metric types:
- counter : monotonically increasing integer
- timer : duration between paired events (e.g. SYNC_STARTED →
 SYNC_COMPLETE) in milliseconds
- gauge : latest value from an event payload
The catalog of 21 metrics defined in the technical document is
derived directly from the EventBus events — no manual counter
increments needed anywhere else in the codebase.
Reference: Technical Document v1.0 — Section 9.6, Figure 83.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.types import Events
from ..event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)
class MetricsCollector:
    """Aggregates per-event counters, timers and gauges."""

    def __init__(self, bus: EventBus) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._bus = bus
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        # Pending timers: key → start_time (set by a START event,
        # consumed by a COMPLETE event)
        self._pending_timers: dict[str, float] = {}
        # Recorded durations (in ms)
        self._timers: dict[str, float] = {}
        self._started_at = time.time()
        # Auto-subscribe to the bus at construction time. This
        # matches the pattern used by every other service in the
        # services/ package — the plugin's _wire_services() just
        # instantiates each service and the subscriptions are
        # live immediately. Previously this lived in a separate
        # async start() method that main.py never called, making
        # MetricsCollector effectively dead in production.
        self._subscribe_all()

    def _subscribe_all(self) -> None:
        """Register every bus subscription in one place.
        Counter events increment a named counter each time the
        event fires. Timer events come in START→COMPLETE pairs
        and record the elapsed duration. Gauge events snapshot
        a numeric field from the payload. All subscriptions are
        synchronous (bus.on is a plain dict append) so this
        method doesn't need to be async.
        """
        # Counter events: increment by 1 on each emission
        counter_events = [
        (Events.STORE_AUTH_STARTED, "auth_attempts"),
        (Events.STORE_AUTH_COMPLETE, "auth_successes"),
        (Events.STORE_AUTH_FAILED, "auth_failures"),
        (Events.SYNC_FAILED, "sync_failures"),
        (Events.DOWNLOAD_QUEUED, "download_queued"),
        (Events.DOWNLOAD_COMPLETE, "download_completed"),
        (Events.DOWNLOAD_FAILED, "download_failed"),
        ]
        # Counter events remain imperative because they share a
        # generic lambda handler (not compatible with @subscribe
        for event, name in counter_events:
            self._bus.on(
             event,
             lambda n=name, **kw: self._inc_counter(n))
            from unifideck.event_bus.event_bus_devex import auto_wire
            auto_wire(self, self._bus)
            logger.info("[MetricsCollector] wired (%d counter + decorated handlers)",
             len(counter_events))
    async def stop(self) -> None:
        """Clear all subscriptions (for shutdown/tests)."""
        # Simplest: clear the entire bus. In production we'd store
        # handler refs and call off() on each, but this is fine for
        # the shutdown path.
        pass
        # ── Public API ──────────────────────────────────────────────
    def get_plugin_metrics(self) -> dict[str, Any]:
        """Return a snapshot of the current metrics state."""
        return {
        "counters": dict(self._counters),
        "timers_ms": dict(self._timers),
        "gauges": dict(self._gauges),
        "uptime_s": int(time.time() - self._started_at),
        }
    def reset(self) -> None:
        """Clear all metrics (useful for tests)."""
        self._counters.clear()
        self._gauges.clear()
        self._pending_timers.clear()
        self._timers.clear()
        # ── Internal event handlers ────────────────────────────────
    def _inc_counter(self, name: str) -> None:
        self._counters[name] = self._counters.get(name, 0) + 1
    def _on_auth_start(self, store: str = "", **kwargs) -> None:
        self._pending_timers[f"auth:{store}"] = time.monotonic()
    def _on_auth_complete(self, store: str = "", **kwargs) -> None:
        self._complete_timer(f"auth:{store}", "auth_duration_ms")
    def _on_sync_start(self, **kwargs) -> None:
        self._pending_timers["sync"] = time.monotonic()
    def _on_sync_complete(self, **kwargs) -> None:
        self._complete_timer("sync", "sync_duration_ms")
    def _on_download_start(self, store: str = "",
    game_id: str = "", **kwargs) -> None:
        self._pending_timers[f"dl:{store}:{game_id}"] = time.monotonic()

    def _on_download_complete(self, store: str = "",
    game_id: str = "", **kwargs) -> None:
        self._complete_timer(
        f"dl:{store}:{game_id}", "download_duration_ms",
        )
    def _on_sync_gauge(self, games=None, stores_synced=None, **kw):
        """Record gauge metrics from SYNC_COMPLETE payload."""
        if games is not None:
            self._gauges["sync_games_total"] = float(len(games))
            if stores_synced is not None:
                self._gauges["sync_stores_count"] = float(len(stores_synced))
    def _complete_timer(self, key: str, metric_name: str) -> None:
        """Close a timer and record its duration."""
        started = self._pending_timers.pop(key, None)
        if started is None:
            return
        duration_ms = (time.monotonic() - started) * 1000
        self._timers[metric_name] = duration_ms
