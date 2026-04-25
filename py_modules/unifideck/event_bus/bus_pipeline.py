"""event_bus/bus_pipeline.py — Assembled EventBus + PriorityDispatcher pipeline.

# OP-09i | event_bus/bus_pipeline.py | Depends: OP-09a, OP-09c, OP-09f, OP-09g, OP-09h, OP-10a, OP-10b
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .event_replay import EventReplayBuffer
    from .priority_dispatcher import PriorityDispatcher
    from .supervision.metrics_handler import HandlerLatencyCollector
    from .supervision.watchdog_handler import HandlerWatchdog

logger = logging.getLogger(__name__)


class BusPipeline:
    """Fully wired bus: EventBus + PriorityDispatcher + supervision."""

    def __init__(
        self,
        bus: EventBus,
        dispatcher: PriorityDispatcher,
        watchdog: HandlerWatchdog | None = None,
        latency: HandlerLatencyCollector | None = None,
        replay: EventReplayBuffer | None = None,
    ) -> None:
        self.bus = bus
        self.dispatcher = dispatcher
        self.watchdog = watchdog
        self.latency = latency
        self.replay = replay

    async def start(self) -> None:
        """Start the dispatcher worker task."""
        await self.dispatcher.start()
        logger.info("[BusPipeline] Started")

    async def stop(self) -> None:
        """Stop the dispatcher and clear the bus."""
        await self.dispatcher.stop()
        self.bus.clear()
        logger.info("[BusPipeline] Stopped")

    def emit(self, event: str, **kwargs: Any) -> bool:
        """Enqueue an event through the priority dispatcher.
        Returns False if the event was dropped (BACKGROUND saturated).
        """
        return self.dispatcher.enqueue(event, **kwargs)

    def get_health(self) -> dict[str, Any]:
        """Aggregate metrics from all pipeline components."""
        health: dict[str, Any] = {}

        # Dispatcher metrics
        dm = self.dispatcher.get_metrics()
        health["dispatcher"] = {
            "emitted_total": dm.emitted_total,
            "dispatched_total": dm.dispatched_total,
            "coalesced_total": dm.coalesced_total,
            "dropped_background_total": dm.dropped_background_total,
            "pending_by_priority": dm.pending_by_priority,
        }

        # Watchdog metrics
        if self.watchdog is not None:
            wm = self.watchdog.get_metrics()
            health["watchdog"] = {
                name: {
                    "invocations": m.invocations,
                    "timeouts": m.timeouts,
                    "quarantined": m.quarantined,
                }
                for name, m in wm.items()
            }

        # Latency metrics
        if self.latency is not None:
            health["latency"] = self.latency.get_snapshot()

        # Replay buffer size
        if self.replay is not None:
            health["replay_entries"] = len(self.replay.snapshot())

        return health
