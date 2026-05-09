"""services/download/worker.py — Worker loop + install dispatch.

Queue consumer: polls pending queue, enforces concurrency cap,
dispatches each install to the right store via the registry,
emits ``DOWNLOAD_{STARTED,COMPLETE,FAILED}``. Mixin — only
touches host state, no I/O primitives of its own.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .models import DownloadItem, classify_download_error

if TYPE_CHECKING:
    from ...event_bus.event_bus import EventBus
    from ...stores import StoreRegistry

logger = logging.getLogger(__name__)


class _WorkerMixin:
    """Queue worker + install dispatcher for DownloadService.

    Attribute declarations satisfy mypy; at runtime they come
    from the host class.
    """

    _bus: EventBus
    _registry: StoreRegistry
    _lock: asyncio.Lock
    _max_concurrent: int
    _queue: list[DownloadItem]
    _running: dict[str, DownloadItem]

    async def _worker_loop(self) -> None:
        """Poll the queue and dispatch installs.

        Runs until cancelled. Each iteration: acquire lock,
        while ``len(running) < max_concurrent and queue``, pop
        next item, spawn ``_run_install`` as a task. Sleep
        briefly between polls so cancellation is responsive.
        """
        while True:
            try:
                # 1. Check if we have capacity and items
                to_start = []
                async with self._lock:
                    while len(self._running) < self._max_concurrent and self._queue:
                        item = self._queue.pop(0)
                        key = f"{item.store}:{item.game_id}"
                        self._running[key] = item
                        to_start.append(item)

                # 2. Start the tasks outside the lock
                # We save the queue so the popped items are persisted as removed
                if to_start:
                    save_method = getattr(self, "_save_queue", None)
                    if callable(save_method):
                        await save_method()

                    for item in to_start:
                        asyncio.create_task(self._run_install(item))

                # 3. Sleep before next poll
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[DownloadWorker] unhandled error in loop: %s", e)
                await asyncio.sleep(5.0)  # Backoff on error

    async def _run_install(self, item: DownloadItem) -> None:
        """Execute one install via ``StoreBase.install_game``.

        Flow: resolve store via registry (missing → emit
        DOWNLOAD_FAILED + cleanup), emit DOWNLOAD_STARTED,
        call ``store.install_game(item.game_id,
        progress_cb=self._update_progress)``, classify the
        result (``InstallResult``) or any exception via
        ``classify_download_error``, emit DOWNLOAD_COMPLETE or
        DOWNLOAD_FAILED with the classified error, always
        ``_cleanup_running(item)`` in a finally block.
        """
        key = f"{item.store}:{item.game_id}"
        from ...core.types.events import Events

        try:
            store = self._registry.get(item.store)
            if not store:
                raise RuntimeError(f"Store {item.store} not found in registry")

            if self._bus:
                self._bus.emit(Events.DOWNLOAD_STARTED, item=item.to_dict())

            # Progress callback wrapper
            def progress_cb(progress_dict: dict[str, Any]) -> None:
                self._update_progress(item, progress_dict)

            # Do the install
            logger.info("[DownloadWorker] starting install for %s", key)
            result = await store.install_game(
                item.game_id,
                item.install_path,
                progress_cb=progress_cb,
            )

            if result.success:
                logger.info("[DownloadWorker] completed install for %s", key)
                if self._bus:
                    self._bus.emit(Events.DOWNLOAD_COMPLETE, item=item.to_dict())
            else:
                error_type = classify_download_error(result.error)
                logger.error("[DownloadWorker] failed install for %s: %s (%s)", key, result.error, error_type)
                if self._bus:
                    self._bus.emit(Events.DOWNLOAD_FAILED, item=item.to_dict(), error=result.error, error_type=error_type)

        except Exception as e:
            error_type = classify_download_error(str(e))
            logger.error("[DownloadWorker] exception during install of %s: %s", key, e)
            if self._bus:
                self._bus.emit(Events.DOWNLOAD_FAILED, item=item.to_dict(), error=str(e), error_type=error_type)
        finally:
            self._cleanup_running(item)

    def _cleanup_running(self, item: DownloadItem) -> None:
        """Remove a finished item from ``self._running``.

        No-op when the key is already gone (idempotent so
        failure paths can call it without tracking state).
        """
        key = f"{item.store}:{item.game_id}"
        # We must use the lock here since the worker loop also accesses _running
        # But this is a sync method, so we have to do it carefully or use a non-blocking remove.
        # Since _running is a dict, del is thread-safe enough in CPython due to GIL,
        # but to be perfectly clean with asyncio we should pop it.
        self._running.pop(key, None)

    def _update_progress(self, item: DownloadItem, progress: dict[str, Any]) -> None:
        """Progress callback invoked from the store's ``install_game``.

        Store progress on the item, emit DOWNLOAD_PROGRESS.
        """
        item.progress = progress
        if self._bus:
            from ...core.types.events import Events
            # We don't emit the full item dict on every progress tick to save IPC overhead,
            # just the identifiers and the progress dict.
            self._bus.emit(
                Events.DOWNLOAD_PROGRESS,
                store=item.store,
                game_id=item.game_id,
                progress=progress,
            )
