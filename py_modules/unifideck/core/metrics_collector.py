"""core/metrics_collector.py — In-memory metrics aggregator.

# OP-04b | core/metrics_collector.py | Depends: OP-09a

Plugin-level observer that subscribes to every EventBus event
and maintains counters/timers/gauges for the diagnostics panel.
Catalog derived directly from EventBus events — no manual
increments needed elsewhere.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..event_bus.event_bus import EventBus
from ..core.types import Events

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Aggregates per-event counters, timers and gauges."""

    def __init__(self, bus: EventBus) -> None:
        """Init empty metric dicts, record start time, auto-subscribe to bus.
        Subscription happens here (not in a separate ``start()``) so
        the collector is live immediately after instantiation.
        """
        self._bus = bus
        self._counters: dict[str, int] = {}
        self._timers_ms: dict[str, float] = {}
        self._gauges: dict[str, Any] = {}
        self._pending_timers: dict[str, float] = {}
        self._start_time = time.monotonic()
        self._subscribe_all()

    def _subscribe_all(self) -> None:
        """Register every bus subscription in one place.
        Counter events increment a named counter each emission.
        Timer events come as START-COMPLETE pairs and record duration.
        Gauge events snapshot a numeric field from the payload.
        """
        # Timer pairs
        self._bus.on(Events.STORE_AUTH_STARTED, self._on_auth_start)
        self._bus.on(Events.STORE_AUTH_COMPLETE, self._on_auth_complete)
        self._bus.on(Events.SYNC_STARTED, self._on_sync_start)
        self._bus.on(Events.SYNC_COMPLETE, self._on_sync_complete)
        self._bus.on(Events.DOWNLOAD_STARTED, self._on_download_start)
        self._bus.on(Events.DOWNLOAD_COMPLETE, self._on_download_complete)

        # Gauge events
        self._bus.on(Events.SYNC_COMPLETE, self._on_sync_gauge)

        # Counter events — increment on each emission
        counter_events = [
            Events.GAME_INSTALLED, Events.GAME_UNINSTALLED,
            Events.STORE_AUTH_FAILED, Events.STORE_LOGOUT,
            Events.DOWNLOAD_FAILED, Events.DOWNLOAD_CANCELLED,
            Events.SYNC_FAILED, Events.SYNC_CANCELLED,
            Events.STORE_ERROR, Events.SHORTCUT_CREATED,
        ]
        for evt in counter_events:
            # Use a closure to capture the event name
            key = evt.value if hasattr(evt, "value") else str(evt)
            self._bus.on(evt, lambda _key=key, **kw: self._inc_counter(_key))

    async def stop(self) -> None:
        """Clear all subscriptions (for shutdown/tests)."""
        self._bus.off(Events.STORE_AUTH_STARTED, self._on_auth_start)
        self._bus.off(Events.STORE_AUTH_COMPLETE, self._on_auth_complete)
        self._bus.off(Events.SYNC_STARTED, self._on_sync_start)
        self._bus.off(Events.SYNC_COMPLETE, self._on_sync_complete)
        self._bus.off(Events.DOWNLOAD_STARTED, self._on_download_start)
        self._bus.off(Events.DOWNLOAD_COMPLETE, self._on_download_complete)

    def get_plugin_metrics(self) -> dict[str, Any]:
        """Return snapshot: ``{counters, timers_ms, gauges, uptime_s}``."""
        return {
            "counters": dict(self._counters),
            "timers_ms": dict(self._timers_ms),
            "gauges": dict(self._gauges),
            "uptime_s": round(time.monotonic() - self._start_time, 1),
        }

    def reset(self) -> None:
        """Clear every metric dict (useful for tests)."""
        self._counters.clear()
        self._timers_ms.clear()
        self._gauges.clear()
        self._pending_timers.clear()

    def _inc_counter(self, name: str) -> None:
        """Increment named counter by 1 (create if missing)."""
        self._counters[name] = self._counters.get(name, 0) + 1

    def _on_auth_start(self, store: str = "", **kwargs) -> None:
        """Start an ``auth:<store>`` timer on STORE_AUTH_STARTED."""
        self._pending_timers[f"auth:{store}"] = time.monotonic()

    def _on_auth_complete(self, store: str = "", **kwargs) -> None:
        """Close ``auth:<store>`` timer, record as ``auth_duration_ms``."""
        self._complete_timer(f"auth:{store}", f"auth_duration_ms:{store}")

    def _on_sync_start(self, **kwargs) -> None:
        """Start the ``sync`` timer on SYNC_STARTED."""
        self._pending_timers["sync"] = time.monotonic()

    def _on_sync_complete(self, **kwargs) -> None:
        """Close ``sync`` timer, record as ``sync_duration_ms``."""
        self._complete_timer("sync", "sync_duration_ms")

    def _on_download_start(
        self, store: str = "", game_id: str = "", **kwargs,
    ) -> None:
        """Start a ``dl:<store>:<game_id>`` timer on DOWNLOAD_STARTED."""
        self._pending_timers[f"dl:{store}:{game_id}"] = time.monotonic()

    def _on_download_complete(
        self, store: str = "", game_id: str = "", **kwargs,
    ) -> None:
        """Close the download timer, record as ``download_duration_ms``."""
        self._complete_timer(f"dl:{store}:{game_id}", f"download_duration_ms:{store}:{game_id}")

    def _on_sync_gauge(self, games=None, stores_synced=None, **kw):
        """Record gauge metrics ``sync_games_total`` + ``sync_stores_count``
        from the SYNC_COMPLETE payload.
        """
        if games is not None:
            self._gauges["sync_games_total"] = len(games) if isinstance(games, list) else games
        if stores_synced is not None:
            self._gauges["sync_stores_count"] = stores_synced

    def _complete_timer(self, key: str, metric_name: str) -> None:
        """Look up the pending timer, compute elapsed ms, store as metric.
        Silently no-op if the START was never seen.
        """
        started_at = self._pending_timers.pop(key, None)
        if started_at is None:
            return
        elapsed_ms = (time.monotonic() - started_at) * 1000
        self._timers_ms[metric_name] = round(elapsed_ms, 1)
