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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.types import Events

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .event_bus_scaling import BatchDispatcher
    from .event_replay import EventReplayBuffer
    from .supervision.watchdog_handler import HandlerWatchdog

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
        raise NotImplementedError("OP-09c: init PriorityQueue, coalesce map, metrics")

    async def start(self) -> None:
        """Spawn the single worker task that drains the queue.
        Idempotent — multiple ``start()`` calls keep the same worker.
        """
        raise NotImplementedError("OP-09c: spawn asyncio task if not running")

    async def stop(self) -> None:
        """Signal the worker to exit, wait for it, flush queue.
        Drains pending CRITICAL events (they are never dropped on
        shutdown). Cancels the worker task cleanly.
        """
        raise NotImplementedError("OP-09c: signal stop, drain CRITICAL, cancel task")

    def enqueue(
        self, event: Events | str, **kwargs: Any,
    ) -> bool:
        """Add an event to the priority queue.
        Return False if the event was dropped (BACKGROUND saturation).
        Coalescing happens before the backpressure check: if a
        matching in-flight event exists, we replace it and return True.
        """
        raise NotImplementedError("OP-09c: coalesce or push, check backpressure")

    def get_metrics(self) -> DispatcherMetrics:
        """Return the current ``DispatcherMetrics`` snapshot.
        Pending counts are recomputed from the live queue each call.
        """
        raise NotImplementedError("OP-09c: compute pending counts, return metrics")

    def _is_saturated(self, priority: EventPriority) -> bool:
        """Return True when BACKGROUND queue length ≥ cap.
        CRITICAL and NORMAL are never saturated — always False for them.
        """
        raise NotImplementedError("OP-09c: check cap only for BACKGROUND priority")

    def _record_drop(self) -> None:
        """Increment drop counter and emit a throttled WARNING.
        Only logs once per ``DROP_WARNING_INTERVAL_SEC`` to avoid
        flooding the journal during a saturation spike.
        """
        raise NotImplementedError("OP-09c: increment counter, throttle log")

    def _coalesce_if_possible(
        self, event: Events | str, kwargs: dict[str, Any],
    ) -> bool:
        """If this event type has a coalesce key and a matching
        in-flight event exists, replace its kwargs in place and
        return True. Otherwise return False so the caller pushes a
        new queue entry.
        """
        raise NotImplementedError("OP-09c: lookup COALESCE_KEY, search queue, replace")

    def _push(
        self, priority: EventPriority, event: Events | str, kwargs: dict[str, Any],
    ) -> None:
        """Create ``_QueueItem`` with the next seq, put on the queue,
        update coalesce map and metrics. Internal — callers go
        through ``enqueue()``.
        """
        raise NotImplementedError("OP-09c: create item, queue.put_nowait, update maps")

    async def _worker(self) -> None:
        """Main drain loop — get next item, dispatch, repeat.
        Runs until cancelled. One item per iteration so priorities
        are respected every tick, not batched.
        """
        raise NotImplementedError("OP-09c: while True: item = await queue.get(); dispatch")

    async def _dispatch_one(self, item: _QueueItem) -> None:
        """Invoke the bus with one item, handle collaborators.
        Removes the item from the coalesce map first, then: watchdog
        timing, batch dispatch if enabled, replay recording, latency
        collection, error isolation via ``_handle_dispatch_error``.
        """
        raise NotImplementedError("OP-09c: bus.emit, watchdog, replay, latency")

    def _handle_dispatch_error(
        self, item: _QueueItem, exc: Exception,
    ) -> None:
        """Log the error with event name + exc type.
        Never re-raises — a failed dispatch must not stop the worker.
        """
        raise NotImplementedError("OP-09c: log ERROR, never raise")
