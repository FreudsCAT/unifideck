"""event_bus/event_bus_scaling.py — Batch dispatcher for bulk events.

# OP-09g | event_bus/event_bus_scaling.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .event_bus import EventBus

logger = logging.getLogger(__name__)


class BatchDispatcher:
    """Accumulate events and flush in batches to reduce overhead."""

    def __init__(self, bus: EventBus, batch_size: int = 50, flush_interval: float = 0.1) -> None:
        self._bus = bus
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list[tuple[str, dict[str, Any]]] = []
        self._flush_task: asyncio.Task | None = None

    async def add(self, event: str, **kwargs: Any) -> None:
        """Add an event to the batch buffer. Auto-flushes at batch_size."""
        self._buffer.append((event, kwargs))
        if len(self._buffer) >= self._batch_size:
            await self.flush()
        elif self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush())

    async def flush(self) -> None:
        """Emit all buffered events immediately."""
        if not self._buffer:
            return
        batch = list(self._buffer)
        self._buffer.clear()
        for event, kwargs in batch:
            await self._bus.emit(event, **kwargs)

    async def _delayed_flush(self) -> None:
        """Wait for flush_interval then flush remaining buffer."""
        await asyncio.sleep(self._flush_interval)
        await self.flush()
