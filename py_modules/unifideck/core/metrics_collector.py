"""Plugin-wide metrics collector — bus-driven counters + timers + gauges.

OP-08i | py_modules/unifideck/core/metrics_collector.py

``MetricsCollector`` subscribes to a fixed set of bus events
and maintains three families of metrics:

* **Counters** — monotonically incrementing tallies
  (auth attempts/successes/failures, download
  queued/completed/failed). Wired declaratively in
  ``_subscribe_all``.
* **Timers**   — last observed duration in milliseconds for
  pair-events: auth start→complete, sync start→complete,
  download start→complete. ``_pending_timers`` holds the
  in-flight start times keyed by the operation; on
  completion the timer is finalised into ``_timers``.
* **Gauges**   — point-in-time floats (current sync game
  count, current store count).

Surfaced via ``get_plugin_metrics`` to the observability RPC
+ the QAM diagnostics tab. Reset via the dev-only ``reset``.

Note: this is the **plugin-level** metrics surface (counters
across all events). Per-handler latency and watchdog state
live in the ``event_bus/supervision`` package.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from unifideck.core.types import Events
from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Bus-driven counters / timers / gauges aggregator."""

    def __init__(self, bus: EventBus) -> None:
        """Initialise state and subscribe to every observed event.

        Three dicts hold the metric state:

        * ``_counters`` — ``str → int``;
        * ``_gauges``   — ``str → float``;
        * ``_pending_timers`` — ``op_key → monotonic_start``
          for in-flight timers; finalised entries move to
          ``_timers``.

        ``_started_at`` captures process start (wall-clock)
        for the ``uptime_s`` field. Subscriptions happen in
        ``_subscribe_all`` so the constructor stays short.

        Args:
            bus: live event bus.
        """
        self._bus = bus
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._pending_timers: dict[str, float] = {}
        self._timers: dict[str, float] = {}
        self._started_at = time.time()
        self._subscribe_all()

    def _subscribe_all(self) -> None:
        """Wire bus subscriptions for every tracked event.

        Two paths used:

        1. **Counter lambdas** — declarative
           ``(event, counter_name)`` table; the lambda
           closure captures the counter name and forwards
           any kwargs (ignored).
        2. **Decorated handlers** — the ``_on_*`` methods
           use ``auto_wire`` so they're picked up by the
           bus's introspection. Note: ``auto_wire`` is
           called inside the loop — should only run once,
           but extra calls are no-ops (idempotent).

        Logs at INFO with the wiring count once
        registration completes.
        """
        counter_events = [
            (Events.STORE_AUTH_STARTED, "auth_attempts"),
            (Events.STORE_AUTH_COMPLETE, "auth_successes"),
            (Events.STORE_AUTH_FAILED, "auth_failures"),
            (Events.SYNC_FAILED, "sync_failures"),
            (Events.DOWNLOAD_QUEUED, "download_queued"),
            (Events.DOWNLOAD_COMPLETE, "download_completed"),
            (Events.DOWNLOAD_FAILED, "download_failed"),
        ]
        for event, name in counter_events:
            self._bus.on(event, lambda n=name, **kw: self._inc_counter(n))
            from unifideck.event_bus.event_bus_devex import auto_wire

            auto_wire(self, self._bus)
            logger.info(
                "[MetricsCollector] wired (%d counter + decorated handlers)",
                len(counter_events),
            )

    async def stop(self) -> None:
        """No-op shutdown hook.

        Kept on the API surface so the service container
        can call it uniformly on every collected service
        during plugin teardown. Subscriptions on the bus
        are cleared by the bus itself when it shuts down.
        """

    def get_plugin_metrics(self) -> dict[str, Any]:
        """Return a shallow snapshot of every metric family.

        Each family is copied (``dict(...)``) to decouple
        the caller from concurrent mutations. ``uptime_s``
        is computed inline from ``_started_at``.

        Returns:
            Dict with four keys: ``counters``, ``timers_ms``,
            ``gauges``, ``uptime_s``.
        """
        return {
            "counters": dict(self._counters),
            "timers_ms": dict(self._timers),
            "gauges": dict(self._gauges),
            "uptime_s": int(time.time() - self._started_at),
        }

    def reset(self) -> None:
        """Clear every metric family (dev / test only).

        Production code shouldn't need this — counters and
        timers are bounded by what fits in plugin lifetime.
        Useful in tests where the same collector instance
        is reused across cases.
        """
        self._counters.clear()
        self._gauges.clear()
        self._pending_timers.clear()
        self._timers.clear()

    def _inc_counter(self, name: str) -> None:
        """Bump ``_counters[name]`` by 1, creating it if absent.

        Args:
            name: counter identifier.
        """
        self._counters[name] = self._counters.get(name, 0) + 1

    def _on_auth_start(self, store: str = "", **kwargs: Any) -> None:
        """Stash the monotonic start time for an auth attempt.

        Keyed by ``"auth:<store>"`` so concurrent auth
        flows on different stores don't clobber each
        other.

        Args:
            store: store identifier from the event payload.
            **kwargs: ignored (other event fields).
        """
        self._pending_timers[f"auth:{store}"] = time.monotonic()

    def _on_auth_complete(self, store: str = "", **kwargs: Any) -> None:
        """Finalise the auth timer for ``store`` into ``auth_duration_ms``.

        Look up the pending start, compute elapsed, store
        as last-observed value. Missing pending entry
        (auth-failed or auth-completed without a matching
        start) is silently ignored.

        Args:
            store: store identifier.
            **kwargs: ignored.
        """
        self._complete_timer(f"auth:{store}", "auth_duration_ms")

    def _on_sync_start(self, **kwargs: Any) -> None:
        """Stash the monotonic start time for a sync.

        Single shared timer key (``"sync"``) — only one
        sync runs at a time across stores.

        Args:
            **kwargs: ignored.
        """
        self._pending_timers["sync"] = time.monotonic()

    def _on_sync_complete(self, **kwargs: Any) -> None:
        """Finalise the sync timer into ``sync_duration_ms``.

        Args:
            **kwargs: ignored.
        """
        self._complete_timer("sync", "sync_duration_ms")

    def _on_download_start(self, store: str = "", game_id: str = "", **kwargs: Any) -> None:
        """Stash the monotonic start time for a download.

        Keyed by ``"dl:<store>:<game_id>"`` so concurrent
        downloads don't conflict.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            **kwargs: ignored.
        """
        self._pending_timers[f"dl:{store}:{game_id}"] = time.monotonic()

    def _on_download_complete(
        self, store: str = "", game_id: str = "", **kwargs: Any,
    ) -> None:
        """Finalise the (store, game_id) download timer.

        Records into ``download_duration_ms``.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            **kwargs: ignored.
        """
        self._complete_timer(
            f"dl:{store}:{game_id}",
            "download_duration_ms",
        )

    def _on_sync_gauge(
        self,
        games: list[Any] | None = None,
        stores_synced: list[str] | None = None,
        **kw: Any,
    ) -> None:
        """Update the sync gauges from the SYNC_COMPLETE payload.

        Two gauges set from the post-sync state:

        * ``sync_games_total``   — count of games in the
          unified library;
        * ``sync_stores_count``  — count of stores that
          contributed.

        Both stored as ``float`` for uniform gauge typing
        (gauges may carry decimals like ratios; ints are
        the common case).

        Args:
            games: list of games (or None — gauge skipped).
            stores_synced: list of stores (or None — gauge
                skipped). Note: only set when ``games`` is
                also non-None, preserving the inner branch
                in the original logic.
            **kw: ignored.
        """
        if games is not None:
            self._gauges["sync_games_total"] = float(len(games))
            if stores_synced is not None:
                self._gauges["sync_stores_count"] = float(len(stores_synced))

    def _complete_timer(self, key: str, metric_name: str) -> None:
        """Compute elapsed time and record into ``_timers[metric_name]``.

        Pop-and-check: if no start was recorded for
        ``key``, silently return (missing pair). Otherwise
        the elapsed ms is the last-observed value for the
        metric (timers track most-recent, not average —
        the latency collector in event_bus does
        percentiles).

        Args:
            key: ``_pending_timers`` lookup key.
            metric_name: ``_timers`` key to write into.
        """
        started = self._pending_timers.pop(key, None)
        if started is None:
            return
        duration_ms = (time.monotonic() - started) * 1000
        self._timers[metric_name] = duration_ms
