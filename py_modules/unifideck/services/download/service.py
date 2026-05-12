"""Download service — queue downloads, run one at a time, persist across restarts.

OP-15a | py_modules/unifideck/services/download/service.py

``DownloadService`` is a single-worker download queue. Items are
added via ``add``, persisted to disk so the queue survives plugin
restarts, and processed one at a time by a background task spawned
in ``start``.

Why single-worker? The Steam Deck's eMMC has limited write
bandwidth and a small TLC cache; running two installs in parallel
typically halves each one's throughput **and** drives the disk into
a thermal throttle. Sequential downloads finish faster overall.

Items are keyed by ``"<store>:<game_id>"`` and de-duped: a second
``add`` call for the same key is rejected to prevent the user from
queuing the same game twice from the UI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...core.types import Events, Result
from ...event_bus.event_bus import EventBus
from ...stores import StoreRegistry
from . import persistence
from .models import DownloadItem
from .validators import item_key, validate_path
from .worker import _WorkerMixin

logger = logging.getLogger(__name__)


class DownloadService(_WorkerMixin):
    """Persistent download queue with a single background worker."""

    def __init__(
        self,
        bus: EventBus,
        registry: StoreRegistry,
        queue_file: str,
        max_concurrent: int = 1,
    ) -> None:
        """Initialise empty queue + worker state.

        The queue is not loaded from disk yet — that happens in
        ``start`` to keep ``__init__`` synchronous.

        Args:
            bus: live event bus on which the service emits
                ``DOWNLOAD_QUEUED`` / ``DOWNLOAD_STARTED`` /
                ``DOWNLOAD_PROGRESS`` / ``DOWNLOAD_COMPLETED`` /
                ``DOWNLOAD_FAILED`` / ``DOWNLOAD_CANCELLED``.
            registry: store registry used by the worker to look up
                the right store for each download item.
            queue_file: absolute path to the queue persistence JSON
                file.
            max_concurrent: future-use knob, currently ignored —
                the worker is always single-threaded.
        """
        self._bus = bus
        self._registry = registry
        self._queue_file = queue_file
        self._max_concurrent = max_concurrent
        self._queue: list[DownloadItem] = []
        self._running: dict[str, DownloadItem] = {}
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Rehydrate the queue from disk and spawn the worker.

        Items left over from a previous plugin session (queued
        before a Decky reload, for instance) are restored so the
        user doesn't have to re-add them after a restart. The
        worker task then picks them up in queue order.
        """
        await self._load_queue()
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info(
            "[DownloadService] started with %d pending",
            len(self._queue),
        )

    async def stop(self) -> None:
        """Cancel the worker task cleanly.

        Any in-progress download has its subprocess killed by the
        worker's ``CancelledError`` handling. The queue state is
        flushed to disk by the worker before it exits so the next
        ``start`` picks up where this one stopped.
        """
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def add(
        self,
        store: str,
        game_id: str,
        install_path: str,
        title: str = "",
    ) -> Result:
        """Enqueue a download for the given game.

        Three-step validation:

        1. **Already running?** Reject with ``already_running``.
        2. **Already queued?** Reject with ``already_queued``.
        3. **Install path valid?** Delegate to ``validate_path``
           which checks writability and free space.

        On success, the item is appended to the queue, the queue
        is persisted, and ``DOWNLOAD_QUEUED`` is emitted.

        Args:
            store: store identifier (must match a registered store).
            game_id: store-specific game id.
            install_path: target install directory.
            title: optional human-readable title (used by the UI).

        Returns:
            ``Result(success=True)`` on enqueue, or
            ``Result(success=False, error=…)`` with one of
            ``already_running`` / ``already_queued`` /
            (validation-specific error).
        """
        key = f"{store}:{game_id}"
        if key in self._running:
            return Result(success=False, error="already_running")
        if any(item_key(i) == key for i in self._queue):
            return Result(success=False, error="already_queued")
        validation = validate_path(install_path)
        if not validation.success:
            return validation
        item = DownloadItem(
            store=store,
            game_id=game_id,
            install_path=install_path,
            title=title,
        )
        async with self._lock:
            self._queue.append(item)
            await self._save_queue()
        await self._bus.emit(
            Events.DOWNLOAD_QUEUED,
            store=store,
            game_id=game_id,
        )
        return Result(success=True)

    async def cancel(self, store: str, game_id: str) -> Result:
        """Remove a queued download or stop a running one.

        Currently only handles the queued case — for the running
        case the user has to wait for the current subprocess to
        finish (or the worker to detect a cancel signal). This is
        intentional: cancelling a download mid-flight leaves
        partial files on disk that must be cleaned up manually.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``Result(success=True)`` if the item was removed from
            the queue, ``Result(success=False,
            error="not_in_queue")`` otherwise.
        """
        key = f"{store}:{game_id}"
        async with self._lock:
            before = len(self._queue)
            self._queue = [i for i in self._queue if item_key(i) != key]
            removed = len(self._queue) < before
            if removed:
                await self._save_queue()
        if removed:
            await self._bus.emit(
                Events.DOWNLOAD_CANCELLED,
                store=store,
                game_id=game_id,
            )
            return Result(success=True)
        return Result(success=False, error="not_in_queue")

    def get_queue(self) -> dict[str, Any]:
        """Return a snapshot of the queue + currently-running items.

        Used by the RPC layer to render the download tab in the
        QAM panel.

        Returns:
            Dict with two keys: ``"queued"`` (list of dicts, one
            per pending item) and ``"running"`` (list of dicts,
            currently always 0 or 1 entry since the worker is
            single-threaded).
        """
        return {
            "queued": [i.to_dict() for i in self._queue],
            "running": [i.to_dict() for i in self._running.values()],
        }

    async def _load_queue(self) -> None:
        """Load the queue from disk into memory at startup.

        Called once by ``start`` before spawning the worker.
        Persistence failures (missing file, corrupt JSON) are
        absorbed into an empty queue — the user keeps a working
        plugin rather than a hard failure.
        """
        self._queue = await persistence.load_queue(self._queue_file)

    async def _save_queue(self) -> None:
        """Persist the current queue to disk.

        Called after every state mutation (``add``, ``cancel``,
        and after each item the worker completes). Atomic write
        via temp + rename ensures the queue can't be left in a
        partial state by a mid-write crash.
        """
        await persistence.save_queue(self._queue_file, self._queue)
