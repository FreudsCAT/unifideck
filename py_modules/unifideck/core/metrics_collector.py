"""core/metrics_collector.py — In-memory metrics aggregator.

# OP-04b | core/metrics_collector.py | Depends: OP-09a

Plugin-level observer that subscribes to every EventBus event
and maintains counters/timers/gauges for the diagnostics panel.
Catalog derived directly from EventBus events — no manual
increments needed elsewhere.
"""
from __future__ import annotations

from typing import Any

from ..event_bus.event_bus import EventBus


class MetricsCollector:
    """Aggregates per-event counters, timers and gauges."""

    def __init__(self, bus: EventBus) -> None:
        """Init empty metric dicts, record start time, auto-subscribe to bus.
        Subscription happens here (not in a separate ``start()``) so
        the collector is live immediately after instantiation.
        """
        raise NotImplementedError("OP-04b: init counters/timers/gauges dicts, subscribe")

    def _subscribe_all(self) -> None:
        """Register every bus subscription in one place.
        Counter events increment a named counter each emission.
        Timer events come as START-COMPLETE pairs and record duration.
        Gauge events snapshot a numeric field from the payload.
        """
        raise NotImplementedError("OP-04b: wire bus.on() for all Events members")

    async def stop(self) -> None:
        """Clear all subscriptions (for shutdown/tests)."""
        raise NotImplementedError("OP-04b: bus.off() all registered handlers")

    def get_plugin_metrics(self) -> dict[str, Any]:
        """Return snapshot: ``{counters, timers_ms, gauges, uptime_s}``."""
        raise NotImplementedError("OP-04b: return dict snapshot of all metric dicts")

    def reset(self) -> None:
        """Clear every metric dict (useful for tests)."""
        raise NotImplementedError("OP-04b: clear all dicts")

    def _inc_counter(self, name: str) -> None:
        """Increment named counter by 1 (create if missing)."""
        raise NotImplementedError("OP-04b: counters[name] = counters.get(name,0) + 1")

    def _on_auth_start(self, store: str = "", **kwargs) -> None:
        """Start an ``auth:<store>`` timer on STORE_AUTH_STARTED."""
        raise NotImplementedError("OP-04b: record start time keyed by store")

    def _on_auth_complete(self, store: str = "", **kwargs) -> None:
        """Close ``auth:<store>`` timer, record as ``auth_duration_ms``."""
        raise NotImplementedError("OP-04b: compute elapsed ms, store in timers")

    def _on_sync_start(self, **kwargs) -> None:
        """Start the ``sync`` timer on SYNC_STARTED."""
        raise NotImplementedError("OP-04b: record sync start time")

    def _on_sync_complete(self, **kwargs) -> None:
        """Close ``sync`` timer, record as ``sync_duration_ms``."""
        raise NotImplementedError("OP-04b: compute elapsed ms, store in timers")

    def _on_download_start(
        self, store: str = "", game_id: str = "", **kwargs,
    ) -> None:
        """Start a ``dl:<store>:<game_id>`` timer on DOWNLOAD_STARTED."""
        raise NotImplementedError("OP-04b: record download start time keyed by store+game_id")

    def _on_download_complete(
        self, store: str = "", game_id: str = "", **kwargs,
    ) -> None:
        """Close the download timer, record as ``download_duration_ms``."""
        raise NotImplementedError("OP-04b: compute elapsed ms, store in timers")

    def _on_sync_gauge(self, games=None, stores_synced=None, **kw):
        """Record gauge metrics ``sync_games_total`` + ``sync_stores_count``
        from the SYNC_COMPLETE payload.
        """
        raise NotImplementedError("OP-04b: update gauges from payload fields")

    def _complete_timer(self, key: str, metric_name: str) -> None:
        """Look up the pending timer, compute elapsed ms, store as metric.
        Silently no-op if the START was never seen.
        """
        raise NotImplementedError("OP-04b: pop pending start, compute elapsed, store")
