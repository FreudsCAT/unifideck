"""event_bus/priority_dispatcher.py — Priority queue + coalescing + backpressure.

# OP-09c | event_bus/priority_dispatcher.py | Depends: OP-09b

Wraps ``EventBus`` with a single-worker ``asyncio.PriorityQueue``:
CRITICAL events dispatch ahead of NORMAL ahead of BACKGROUND.
Idempotent events (SYNC_PROGRESS, DOWNLOAD_PROGRESS) are coalesced
by a key so thousands of ticks collapse into few dispatches. The
BACKGROUND queue is bounded — drops beyond the cap are counted and
a throttled WARNING is logged once per minute.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.types import Events
from .event_priority import EventPriority, get_coalesce_key, get_priority

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .event_bus_scaling import BatchDispatcher
    from .event_replay import EventReplayBuffer
    from .supervision.watchdog_handler import HandlerWatchdog

logger = logging.getLogger(__name__)

DEFAULT_BACKGROUND_CAP = 500
DROP_WARNING_INTERVAL_SEC = 60.0


@dataclass(order=True)
class _QueueItem:
    """Single event waiting in the dispatch queue.
    Sort order is ``(priority, seq)`` so identical priorities
    preserve FIFO. Non-comparable fields via ``compare=False``.
    """
    priority: int
    seq: int
    event: Events | str | None = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False)
    dropped: bool = field(default=False, compare=False)


@dataclass
class DispatcherMetrics:
    """Observable state of the dispatcher for ``get_bus_health()``.
    ``dropped_background_total`` is a lifetime counter;
    ``pending_by_priority`` is a live snapshot.
    """
    emitted_total: int = 0
    dispatched_total: int = 0
    coalesced_total: int = 0
    dropped_background_total: int = 0
    pending_by_priority: dict[str, int] = field(
        default_factory=lambda: {
            "CRITICAL": 0, "NORMAL": 0, "BACKGROUND": 0,
        },
    )


class PriorityDispatcher:
    """Schedules event dispatches through a priority queue."""

    def __init__(
        self,
        bus: EventBus,
        *,
        background_cap: int = DEFAULT_BACKGROUND_CAP,
        watchdog: HandlerWatchdog | None = None,
        latency_collector: Any = None,
        replay_buffer: EventReplayBuffer | None = None,
        batch_dispatcher: BatchDispatcher | None = None,
    ) -> None:
        """Init the priority queue, coalesce map, metrics, and refs
        to optional collaborators (watchdog, latency, replay, batcher).
        Worker task is NOT started here — call ``start()`` explicitly
        so the bus can be constructed in non-async context.
        """
        self._bus = bus
        self._background_cap = background_cap
        self._watchdog = watchdog
        self._latency_collector = latency_collector
        self._replay_buffer = replay_buffer
        self._batch_dispatcher = batch_dispatcher

        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._seq = 0
        self._bg_count = 0
        self._coalesce_map: dict[str, _QueueItem] = {}
        self._metrics = DispatcherMetrics()
        self._worker_task: asyncio.Task | None = None
        self._stopping = False
        self._last_drop_warn: float = 0

    async def start(self) -> None:
        """Spawn the single worker task that drains the queue.
        Idempotent — multiple ``start()`` calls keep the same worker.
        """
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Signal the worker to exit, wait for it, flush queue.
        Drains pending CRITICAL events (they are never dropped on
        shutdown). Cancels the worker task cleanly.
        """
        self._stopping = True
        if self._worker_task is not None:
            # Push a sentinel to unblock the worker
            self._queue.put_nowait(_QueueItem(
                priority=999, seq=self._seq, event=None, kwargs={},
            ))
            self._seq += 1
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
            self._worker_task = None

    def enqueue(
        self, event: Events | str, **kwargs: Any,
    ) -> bool:
        """Add an event to the priority queue.
        Return False if the event was dropped (BACKGROUND saturation).
        Coalescing happens before the backpressure check: if a
        matching in-flight event exists, we replace it and return True.
        """
        self._metrics.emitted_total += 1
        priority = get_priority(event)

        # Try coalescing first
        if self._coalesce_if_possible(event, kwargs):
            self._metrics.coalesced_total += 1
            return True

        # Backpressure: drop BACKGROUND if saturated
        if self._is_saturated(priority):
            self._record_drop()
            return False

        self._push(priority, event, kwargs)
        return True

    def get_metrics(self) -> DispatcherMetrics:
        """Return the current ``DispatcherMetrics`` snapshot.
        Pending counts are recomputed from the live queue each call.
        """
        # Recompute pending by priority (approximate — queue is concurrent)
        counts = {"CRITICAL": 0, "NORMAL": 0, "BACKGROUND": 0}
        counts["BACKGROUND"] = self._bg_count
        self._metrics.pending_by_priority = counts
        return self._metrics

    def _is_saturated(self, priority: EventPriority) -> bool:
        """Return True when BACKGROUND queue length ≥ cap.
        CRITICAL and NORMAL are never saturated — always False for them.
        """
        if priority != EventPriority.BACKGROUND:
            return False
        return self._bg_count >= self._background_cap

    def _record_drop(self) -> None:
        """Increment drop counter and emit a throttled WARNING.
        Only logs once per ``DROP_WARNING_INTERVAL_SEC`` to avoid
        flooding the journal during a saturation spike.
        """
        self._metrics.dropped_background_total += 1
        now = time.monotonic()
        if now - self._last_drop_warn > DROP_WARNING_INTERVAL_SEC:
            self._last_drop_warn = now
            logger.warning(
                "[PriorityDispatcher] BACKGROUND queue saturated — "
                "dropped %d events total",
                self._metrics.dropped_background_total,
            )

    def _coalesce_if_possible(
        self, event: Events | str, kwargs: dict[str, Any],
    ) -> bool:
        """If this event type has a coalesce key and a matching
        in-flight event exists, replace its kwargs in place and
        return True. Otherwise return False so the caller pushes a
        new queue entry.
        """
        coalesce_key_name = get_coalesce_key(event)
        if not coalesce_key_name:
            return False

        key_value = kwargs.get(coalesce_key_name, "")
        event_str = event.value if hasattr(event, "value") else str(event)
        map_key = f"{event_str}:{key_value}"

        existing = self._coalesce_map.get(map_key)
        if existing is not None and not existing.dropped:
            # Replace kwargs in-place on the existing queue item
            existing.kwargs = kwargs
            return True
        return False

    def _push(
        self, priority: EventPriority, event: Events | str, kwargs: dict[str, Any],
    ) -> None:
        """Create ``_QueueItem`` with the next seq, put on the queue,
        update coalesce map and metrics. Internal — callers go
        through ``enqueue()``.
        """
        item = _QueueItem(
            priority=int(priority),
            seq=self._seq,
            event=event,
            kwargs=kwargs,
        )
        self._seq += 1

        # Update coalesce map if applicable
        coalesce_key_name = get_coalesce_key(event)
        if coalesce_key_name:
            key_value = kwargs.get(coalesce_key_name, "")
            event_str = event.value if hasattr(event, "value") else str(event)
            map_key = f"{event_str}:{key_value}"
            self._coalesce_map[map_key] = item

        if priority == EventPriority.BACKGROUND:
            self._bg_count += 1

        self._queue.put_nowait(item)

    async def _worker(self) -> None:
        """Main drain loop — get next item, dispatch, repeat.
        Runs until cancelled. One item per iteration so priorities
        are respected every tick, not batched.
        """
        while True:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break

            # Sentinel check for shutdown
            if item.event is None:
                self._queue.task_done()
                if self._stopping:
                    break
                continue

            if item.dropped:
                self._queue.task_done()
                continue

            try:
                await self._dispatch_one(item)
            except Exception as exc:
                self._handle_dispatch_error(item, exc)
            finally:
                self._queue.task_done()

            if self._stopping and self._queue.empty():
                break

    async def _dispatch_one(self, item: _QueueItem) -> None:
        """Invoke the bus with one item, handle collaborators.
        Removes the item from the coalesce map first, then: watchdog
        timing, batch dispatch if enabled, replay recording, latency
        collection, error isolation via ``_handle_dispatch_error``.
        """
        event = item.event
        kwargs = item.kwargs

        # Remove from coalesce map
        coalesce_key_name = get_coalesce_key(event)
        if coalesce_key_name:
            key_value = kwargs.get(coalesce_key_name, "")
            event_str = event.value if hasattr(event, "value") else str(event)
            map_key = f"{event_str}:{key_value}"
            self._coalesce_map.pop(map_key, None)

        if item.priority == EventPriority.BACKGROUND:
            self._bg_count = max(0, self._bg_count - 1)

        # Dispatch through the bus
        t0 = time.monotonic()
        await self._bus.emit(event, **kwargs)
        elapsed_ms = (time.monotonic() - t0) * 1000

        self._metrics.dispatched_total += 1

        # Record to replay buffer if available
        if self._replay_buffer is not None:
            self._replay_buffer.record(event, kwargs)

        # Record latency if collector available
        if self._latency_collector is not None:
            event_str = event.value if hasattr(event, "value") else str(event)
            self._latency_collector.record(event_str, elapsed_ms)

    def _handle_dispatch_error(
        self, item: _QueueItem, exc: Exception,
    ) -> None:
        """Log the error with event name + exc type.
        Never re-raises — a failed dispatch must not stop the worker.
        """
        logger.error(
            "[PriorityDispatcher] Dispatch failed for %s: %s",
            item.event, exc,
        )
