"""services/download/service.py — Central download queue + dispatcher.

Refactor of legacy download/manager.py. Queue accepting install
requests from the frontend, dispatching to the appropriate
``StoreBase`` via ``StoreRegistry``. Polymorphic — no per-store
branching; worker mixin handles the consumer loop.

Persists the queue so pending downloads survive plugin restarts.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Result

from .models import DownloadItem
from .persistence import load_queue, save_queue
from .validators import validate_path
from .worker import _WorkerMixin

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)


class DownloadService(_WorkerMixin):
    """Queue + dispatcher for store-agnostic game installations."""

    def __init__(
        self,
        bus: EventBus,
        registry: StoreRegistry,
        queue_file: str,
        max_concurrent: int = 1,
        launcher_path: str = "",
    ) -> None:
        """Store refs + queue path, init empty state."""
        self._bus = bus
        self._registry = registry
        self._queue_file = queue_file
        self._max_concurrent = max_concurrent
        self._launcher_path = launcher_path

        self._queue: list[DownloadItem] = []
        self._running: dict[str, DownloadItem] = {}
        # Mirror of ``_running`` keyed the same way, pointing at
        # the asyncio.Task driving each install. Lets ``cancel()``
        # actually kill a running download instead of returning
        # ``already_running`` — the worker catches CancelledError,
        # emits DOWNLOAD_CANCELLED, and runs its own cleanup.
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        # Recent finished items (capped FIFO). Populated by the
        # worker's ``_cleanup_running``; surfaced to the frontend
        # via ``get_queue()["finished"]`` so the Downloads page can
        # show a history of completed / failed / cancelled installs.
        self._finished: list[DownloadItem] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[Any] | None = None
        self._on_complete_callback: Any = None

    async def start(self) -> None:
        """Load persisted queue + start the worker loop task."""
        if self._task is not None and not self._task.done():
            return

        await self._load_queue()

        # Emit queued event for all items restored from disk
        if self._bus:
            from unifideck.core.types.events import Events
            for item in self._queue:
                await self._bus.emit(Events.DOWNLOAD_QUEUED, item=item.to_dict())

        self._task = asyncio.create_task(self._worker_loop())
        logger.info("[DownloadService] worker task started, %d items in queue", len(self._queue))

    async def stop(self) -> None:
        """Stop the worker loop — does NOT cancel running downloads.

        Cancels the worker task so new items won't dispatch;
        in-flight installs complete or fail on their own. Queue
        is persisted one last time to capture the final state.
        """
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("[DownloadService] worker task stopped")

        await self._save_queue()

    async def add(
        self,
        store: str,
        game_id: str,
        install_path: str,
        title: str = "",
        is_update: bool = False,
    ) -> Result:
        """Queue a new download request.

        ``is_update`` is recorded as-is on the item — the caller
        (the ``install_game`` vs ``update_game`` RPC) knows the
        operation; the service does not infer it.
        """
        # 1. Validation
        val_result = validate_path(install_path)
        if not val_result.success:
            return val_result

        key = f"{store}:{game_id}"

        async with self._lock:
            # 2. Duplicate check
            if key in self._running:
                return Result(success=False, error="already_running")

            for item in self._queue:
                if item.store == store and item.game_id == game_id:
                    return Result(success=False, error="already_queued")

            # 3. Add to queue
            item = DownloadItem(
                store=store,
                game_id=game_id,
                install_path=install_path,
                title=title,
                is_update=is_update,
            )
            self._queue.append(item)

        # 4. Persist and emit outside the lock
        await self._save_queue()

        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(Events.DOWNLOAD_QUEUED, item=item.to_dict())

        return Result(success=True)

    async def cancel(
        self,
        store: str,
        game_id: str,
    ) -> Result:
        """Cancel a download — pending OR running.

        - Pending (in ``_queue``): remove + emit CANCELLED.
        - Running (in ``_running_tasks``): cancel the task; the
          worker's ``_run_install`` catches ``CancelledError``,
          marks the item, emits CANCELLED, and runs
          ``_cleanup_running`` in its finally block.
        """
        key = f"{store}:{game_id}"

        # Running case first — kill the task outside the lock so
        # the worker's cleanup (which itself takes locks) can run.
        running_task = self._running_tasks.get(key)
        if running_task is not None and not running_task.done():
            running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await running_task
            return Result(success=True)

        async with self._lock:
            found_idx = -1
            for i, item in enumerate(self._queue):
                if item.store == store and item.game_id == game_id:
                    found_idx = i
                    break

            if found_idx == -1:
                return Result(success=False, error="not_found")

            item = self._queue.pop(found_idx)
            item.status = "cancelled"

        await self._save_queue()

        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(Events.DOWNLOAD_CANCELLED, item=item.to_dict())

        return Result(success=True)

    def get_queue(self) -> dict[str, Any]:
        """Return current state for the frontend.

        Keys match the frontend ``DownloadQueueInfo`` shape:
        ``current`` (active download or null), ``queued``
        (pending items), ``finished`` (history), ``state``
        (``"idle"`` / ``"running"``). Also keeps ``running``
        for backward compatibility with any internal consumers.
        """
        running_items = list(self._running.values())
        current = running_items[0] if running_items else None
        # Newest-first: callers typically render the most recent
        # completion at the top of the Finished section.
        finished_items = list(reversed(self._finished))
        return {
            "current": current.to_dict() if current else None,
            "queued": [item.to_dict() for item in self._queue],
            "running": [item.to_dict() for item in running_items],
            "finished": [item.to_dict() for item in finished_items],
            "state": "running" if running_items else "idle",
        }

    def set_on_complete_callback(self, callback: Any) -> None:
        """Register a post-install callback invoked by the worker.

        The callback receives the completed ``DownloadItem`` and
        should handle game registration (write ``.unifideck-id``
        marker, update ``games.map``, invalidate caches, trigger
        a sync reconcile, etc.).
        """
        self._on_complete_callback = callback

    async def _load_queue(self) -> None:
        """Replace in-memory queue with the persisted file."""
        try:
            self._queue = await load_queue(self._queue_file)
        except Exception as e:
            logger.warning("[DownloadService] failed to load queue, starting fresh: %s", e)
            self._queue = []

    async def _save_queue(self) -> None:
        """Flush in-memory queue to disk."""
        try:
            # Note: We only persist pending items, not running ones, because
            # a restart interrupts running installs anyway.
            await save_queue(self._queue_file, self._queue)
        except Exception as e:
            logger.warning("[DownloadService] failed to save queue: %s", e)
