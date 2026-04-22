"""event_bus/bus_pipeline.py — Assembled EventBus + PriorityDispatcher pipeline.
# OP-09i | Depends: OP-09a, OP-09c, OP-09f, OP-09g, OP-09h, OP-10a, OP-10b
"""
from __future__ import annotations
from typing import Any


class BusPipeline:
    """Fully wired bus: EventBus + PriorityDispatcher + supervision."""

    def __init__(self, bus, dispatcher, watchdog=None, latency=None, replay=None) -> None:
        raise NotImplementedError("OP-09i: store all components")

    async def start(self) -> None:
        raise NotImplementedError("OP-09i: start dispatcher worker")

    async def stop(self) -> None:
        raise NotImplementedError("OP-09i: stop dispatcher, clear bus")

    def emit(self, event: str, **kwargs: Any) -> None:
        raise NotImplementedError("OP-09i: dispatcher.enqueue(event, **kwargs)")

    def get_health(self) -> dict[str, Any]:
        raise NotImplementedError("OP-09i: aggregate metrics from all components")
