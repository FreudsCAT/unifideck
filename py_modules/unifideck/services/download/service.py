"""Download service orchestration.

OP-15a | py_modules/unifideck/services/download/service.py

``DownloadService`` exposes the public API for queuing, pausing,
resuming, and cancelling downloads :

* ``enqueue(item)`` — add to the queue (persisted, survives restart);
* ``pause(item_key)`` / ``resume(item_key)`` — control individual items;
* ``cancel(item_key)`` — drop an item;
* ``items()`` — snapshot of the queue for the UI;
* ``current()`` — the item currently downloading (at most one).

Downloads run one at a time — competing parallel downloads on the
limited Steam Deck eMMC bandwidth would slow each individual download
and risk filling the SSD's TLC cache.
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
    """Download service."""

    def __init__(
        self,
        bus: EventBus,
        registry: StoreRegistry,
        queue_file: str,
        max_concurrent: int = 1,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._registry = registry
        self._queue_file = queue_file
        self._max_concurrent = max_concurrent
        self._queue: list[DownloadItem] = []
        self._running: dict[str, DownloadItem] = {}
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start."""
        await self._load_queue()
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info(
            "[DownloadService] started with %d pending",
            len(self._queue),
        )

    async def stop(self) -> None:
        """Stop."""
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
        """Add."""
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
        """Check whether ncel."""
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
        """Get queue."""
        return {
            "queued": [i.to_dict() for i in self._queue],
            "running": [i.to_dict() for i in self._running.values()],
        }

    async def _load_queue(self) -> None:
        """Load queue."""
        self._queue = await persistence.load_queue(self._queue_file)

    async def _save_queue(self) -> None:
        """Save queue."""
        await persistence.save_queue(self._queue_file, self._queue)
