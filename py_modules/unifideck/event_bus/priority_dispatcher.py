"""event_bus/priority_dispatcher.py — Priority queue + coalescing + backpressure.

  1. A priority queue so CRITICAL events jump ahead of BACKGROUND
  2. Coalescing of idempotent events (SYNC_PROGRESS, DOWNLOAD_PROGRESS)
  3. Bounded BACKGROUND queue with silent drop + metrics counter
  4. Throttled WARNING log (one per minute) when drops occur

Why a separate class rather than patching EventBus directly:
  - SRP: EventBus handles pub/sub; PriorityDispatcher handles
    scheduling. Two files, two test suites, two concerns.
  - Backward compatibility: existing code that calls `bus.emit()`
    keeps working unchanged. The dispatcher wires itself as an
    emit interceptor, not a replacement.
  - Testability: dispatcher can be exercised with a mock bus that
    just records calls.

Size limit: this module stays below 200 lines and every method is
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.types import Events
from .event_priority import (
    EventPriority,
    get_coalesce_key,
    get_priority,
)

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .event_bus_scaling import BatchDispatcher
    from .event_replay import EventReplayBuffer
    from .supervision.watchdog_handler import HandlerWatchdog

logger = logging.getLogger(__name__)


# Max pending BACKGROUND events. Above this threshold, new
# BACKGROUND events are dropped silently and a metrics counter is
# incremented. CRITICAL and NORMAL queues are unbounded — losing
# them would break correctness.
DEFAULT_BACKGROUND_CAP = 500

# Minimum seconds between consecutive drop-warning log lines.
# Prevents log flood when the queue saturates for a long time.
DROP_WARNING_INTERVAL_SEC = 60.0


@dataclass(order=True)
class _QueueItem:
    """A single event waiting in the dispatch queue.

    Order is (priority, seq) so identical priorities preserve FIFO.
    Kwargs and event name are kept out of the comparison by using
    `field(compare=False)`.
    """

    priority: int
    seq: int
    event: Events | str | None = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False)
    dropped: bool = field(default=False, compare=False)


@dataclass
class DispatcherMetrics:
    """Observable state of the dispatcher for get_bus_health().

    `dropped_background_total` is the lifetime counter — it only
    grows. `pending_by_priority` is a snapshot of the live queue.
    """

    emitted_total: int = 0
    dispatched_total: int = 0
    coalesced_total: int = 0
    dropped_background_total: int = 0
    pending_by_priority: dict[str, int] = field(
        default_factory=lambda: {"CRITICAL": 0, "NORMAL": 0, "BACKGROUND": 0},
    )


class PriorityDispatcher:
    """Schedules event dispatches through a priority queue.

    Usage:
        bus = EventBus()
        dispatcher = PriorityDispatcher(bus)
        await dispatcher.start()          # spawns the worker task
        await dispatcher.enqueue(
            Events.SYNC_PROGRESS, store="epic", progress=42,
        )
        # ... later, at plugin shutdown:
        await dispatcher.stop()
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        bus: EventBus,
        *,
        background_cap: int = DEFAULT_BACKGROUND_CAP,
        watchdog: HandlerWatchdog | None = None,
        latency_collector: Any = None,
        replay_buffer: EventReplayBuffer | None = None,
        batch_dispatcher: BatchDispatcher | None = None,
    ) -> None:
        self._bus = bus
        self._background_cap = background_cap
        self._watchdog = watchdog
        self._latency = latency_collector
        self._replay = replay_buffer
        self._batcher = batch_dispatcher
        self._queue: asyncio.PriorityQueue[_QueueItem] = (
            asyncio.PriorityQueue()
        )
        self._coalesce_map: dict[tuple[str, str], _QueueItem] = {}
        self._seq = 0
        self._metrics = DispatcherMetrics()
        self._last_drop_warn: float = 0.0
        self._worker_task: asyncio.Task | None = None
        self._stopping = False

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the background worker. Idempotent."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="priority-dispatcher",
        )

    async def stop(self) -> None:
        """Drain the queue and stop the worker. Idempotent."""
        self._stopping = True
        if self._worker_task is None:
            return
        # Poison pill: push a sentinel so the worker wakes up and
        # checks the _stopping flag even if the queue is empty.
        await self._queue.put(
            _QueueItem(
                priority=int(EventPriority.CRITICAL),
                seq=-1,
                event=None,
                kwargs={},
            ),
        )
        try:
            await asyncio.wait_for(self._worker_task, timeout=5.0)
        except TimeoutError:
            logger.warning(
                "[PriorityDispatcher] worker did not stop in 5s — "
                "cancelling",
            )
            self._worker_task.cancel()
        self._worker_task = None

    # ── Public API ───────────────────────────────────────────────

    def enqueue(
        self,
        event: Events | str,
        *,
        priority: EventPriority | None = None,
        **kwargs: Any,
    ) -> bool:
        """Schedule an event for dispatch. Returns True if accepted.

        Returns False only when the BACKGROUND queue is saturated
        and this event was dropped. CRITICAL and NORMAL always
        return True. Synchronous by design — does not await the
        actual handler invocation.
        """
        self._metrics.emitted_total += 1
        prio = priority if priority is not None else get_priority(event)

        if self._is_saturated(prio):
            self._record_drop()
            return False

        if self._coalesce_if_possible(event, prio, kwargs):
            return True

        self._push(event, prio, kwargs)
        return True

    def get_metrics(self) -> DispatcherMetrics:
        """Return a snapshot of dispatcher metrics for health RPC."""
        # Recompute pending counts from the live queue. The queue
        # is single-threaded (asyncio) so this snapshot is race-free.
        pending = {"CRITICAL": 0, "NORMAL": 0, "BACKGROUND": 0}
        for item in list(self._queue._queue):  # type: ignore[attr-defined]
            if item.dropped:
                continue
            name = EventPriority(item.priority).name
            pending[name] = pending.get(name, 0) + 1
        self._metrics.pending_by_priority = pending
        return self._metrics

    # ── Internal helpers ─────────────────────────────────────────

    def _is_saturated(self, prio: EventPriority) -> bool:
        """Return True if a BACKGROUND event must be dropped."""
        if prio != EventPriority.BACKGROUND:
            return False
        # Count only non-dropped BACKGROUND items in the queue.
        # Summing the boolean predicate directly sidesteps a mypy
        # typing quirk on `sum(1 for ...)` in this cross-module
        # context.
        pending_bg = sum(
            not item.dropped
            and item.priority == int(EventPriority.BACKGROUND)
            for item in list(self._queue._queue)  # type: ignore[attr-defined]
        )
        return pending_bg >= self._background_cap

    def _record_drop(self) -> None:
        """Increment drop counter + emit a throttled WARNING."""
        self._metrics.dropped_background_total += 1
        now = time.monotonic()
        if now - self._last_drop_warn >= DROP_WARNING_INTERVAL_SEC:
            self._last_drop_warn = now
            logger.warning(
                "[PriorityDispatcher] BACKGROUND queue saturated — "
                "dropped %d events total (cap=%d)",
                self._metrics.dropped_background_total,
                self._background_cap,
            )
        logger.debug(
            "[PriorityDispatcher] drop #%d",
            self._metrics.dropped_background_total,
        )

    def _coalesce_if_possible(
        self,
        event: Events | str,
        prio: EventPriority,
        kwargs: dict[str, Any],
    ) -> bool:
        """Try to replace a pending event with the same coalesce key.

        Returns True if a coalesce happened (caller is done), False
        if the event must be pushed as a new queue entry.
        """
        key_name = get_coalesce_key(event)
        if not key_name or key_name not in kwargs:
            return False
        event_str = event.value if isinstance(event, Events) else str(event)
        coalesce_map_key = (event_str, str(kwargs[key_name]))
        existing = self._coalesce_map.get(coalesce_map_key)
        if existing is None or existing.dropped:
            return False
        # Replace: mark the old one as dropped, push a new one
        existing.dropped = True
        self._metrics.coalesced_total += 1
        self._push(event, prio, kwargs, coalesce_map_key)
        return True

    def _push(
        self,
        event: Events | str,
        prio: EventPriority,
        kwargs: dict[str, Any],
        coalesce_map_key: tuple[str, str] | None = None,
    ) -> None:
        """Push a fresh item onto the queue."""
        self._seq += 1
        item = _QueueItem(
            priority=int(prio),
            seq=self._seq,
            event=event,
            kwargs=kwargs,
        )
        self._queue.put_nowait(item)
        if coalesce_map_key is None:
            # Register for future coalescing if applicable
            key_name = get_coalesce_key(event)
            if key_name and key_name in kwargs:
                event_str = (
                    event.value if isinstance(event, Events) else str(event)
                )
                coalesce_map_key = (event_str, str(kwargs[key_name]))
        if coalesce_map_key is not None:
            self._coalesce_map[coalesce_map_key] = item

    async def _worker(self) -> None:
        """Background task: drain the queue and invoke the bus."""
        while not self._stopping:
            item = await self._queue.get()
            try:
                if self._stopping and item.seq == -1:
                    return
                if item.dropped:
                    continue
                await self._dispatch_one(item)
            except Exception as e:  # noqa: BLE001 — worker loop must survive
                self._handle_dispatch_error(item, e)
            finally:
                self._queue.task_done()

    async def _dispatch_one(self, item: _QueueItem) -> None:
        """Dispatch a single queue item through the full pipeline.

        Split from _worker to keep both under the 60-line norm.
        Measures latency, records in replay + recorder, dispatches
        via the bus. If a BatchDispatcher is configured and the
        event is coalesceable (high-frequency by definition), it
        accumulates items and flushes them as a list to the bus
        via a synthetic `<event>_batch` suffix — handlers that
        opt in receive the full window at once.
        """
        import time
        if item.event is None:
            # Stop sentinel: the worker's seq==-1 short-circuit
            # normally intercepts this, but defend in depth.
            return
        event_str = (
            item.event.value
            if isinstance(item.event, Events)
            else str(item.event)
        )
        t0 = time.monotonic()
        if self._batcher is not None and get_coalesce_key(item.event):
            should_flush = self._batcher.add(event_str, item.kwargs)
            if should_flush:
                batch = self._batcher.drain(event_str)
                await self._bus.emit(
                    f"{event_str}_batch", batch=batch,
                )
        else:
            await self._bus.emit(item.event, **item.kwargs)
        duration_ms = (time.monotonic() - t0) * 1000
        self._metrics.dispatched_total += 1
        if self._latency is not None:
            self._latency.record(event_str, duration_ms)
        if self._replay is not None:
            self._replay.record(item.event, item.kwargs)

    def _handle_dispatch_error(
        self, item: _QueueItem, err: Exception,
    ) -> None:
        """Log the error. Errors never propagate out of the worker."""
        logger.exception(
            "[PriorityDispatcher] handler error on %s: %s",
            item.event, err,
        )
