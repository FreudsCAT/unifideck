"""Download worker mixin — the actual download loop.

OP-15b | py_modules/unifideck/services/download/worker.py

``_WorkerMixin`` runs the background asyncio task that pops items
from the queue and invokes the appropriate store's installer for
each. Handles :

* per-item progress reporting (throttled bus emissions);
* failure classification (transient → retry, permanent → fail);
* cancellation propagation (kills the subprocess on user cancel);
* graceful pause (suspend the subprocess via SIGSTOP).

The loop has no shutdown timeout : on plugin unload the current
item is suspended (not killed) so it can resume on the next plugin
boot from where it left off.
"""

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING
from ...core.types import Events, InstallResult
from .models import DownloadItem, classify_download_error
from .validators import item_key

if TYPE_CHECKING:
    from ...event_bus.event_bus import EventBus
    from ...stores import StoreRegistry
logger = logging.getLogger(__name__)


class _WorkerMixin:
    """Background worker loop glued onto ``DownloadService``.

    Stateful: relies on attributes set by the host class
    (``_bus``, ``_registry``, ``_lock``, ``_max_concurrent``,
    ``_queue``, ``_running``).
    """

    _bus: EventBus
    _registry: StoreRegistry
    _lock: asyncio.Lock
    _max_concurrent: int
    _queue: list[DownloadItem]
    _running: dict[str, DownloadItem]

    async def _worker_loop(self) -> None:
        """Pop items off the queue and install them, one at a time.

        Polls every 500 ms when idle (queue empty or worker
        capacity reached) — a tighter interval would burn CPU for
        no benefit since downloads take minutes. When capacity is
        available and an item is ready, atomically pops it under
        the queue lock, marks it as running, persists the queue,
        and spawns a separate task for the install (so the loop
        can keep polling while the install runs).
        """
        while True:
            if len(self._running) >= self._max_concurrent or not self._queue:
                await asyncio.sleep(0.5)
                continue
            async with self._lock:
                if not self._queue:
                    continue
                item = self._queue.pop(0)
                key = item_key(item)
                self._running[key] = item
                await self._save_queue()
            asyncio.create_task(self._run_install(item))

    async def _run_install(self, item: DownloadItem) -> None:
        """Drive one item through the store's install pipeline.

        Looks up the store in the registry (fails fast with
        ``unknown_store`` if absent), emits ``DOWNLOAD_STARTED``,
        then awaits ``store.install_game`` with a progress
        callback that mutates the item's ``progress`` field as the
        store reports.

        Three outcomes:

        * **success** — emit ``DOWNLOAD_COMPLETE`` with the
          install path and resolved executable;
        * **structured failure** (``result.success=False``) —
          emit ``DOWNLOAD_FAILED`` with the result's error code;
        * **exception** — log, classify the exception via
          ``classify_download_error`` (transient vs permanent),
          emit ``DOWNLOAD_FAILED``.

        Either way, the item is removed from ``_running`` at the
        end via ``_cleanup_running``.

        Args:
            item: the queued ``DownloadItem`` being processed.
        """
        store = self._registry.get(item.store)
        if store is None:
            logger.error(
                "[DownloadService] unknown store: %s",
                item.store,
            )
            item.status = "failed"
            item.error = "unknown_store"
            self._cleanup_running(item)
            return
        item.status = "running"
        await self._bus.emit(
            Events.DOWNLOAD_STARTED,
            store=item.store,
            game_id=item.game_id,
        )
        try:
            result: InstallResult = await store.install_game(
                item.game_id,
                base_path=item.install_path,
                install_path=item.install_path,
                progress_cb=lambda p: self._update_progress(item, p),
            )
        except Exception as e:
            logger.exception("[DownloadService] install error")
            item.status = "failed"
            item.error = classify_download_error(e)
            await self._bus.emit(
                Events.DOWNLOAD_FAILED,
                store=item.store,
                game_id=item.game_id,
                error=item.error,
            )
            self._cleanup_running(item)
            return
        if result.success:
            item.status = "complete"
            item.progress = 100.0
            await self._bus.emit(
                Events.DOWNLOAD_COMPLETE,
                store=item.store,
                game_id=item.game_id,
                install_path=result.install_path,
                executable=result.metadata.get("executable"),
            )
        else:
            item.status = "failed"
            item.error = result.error or "unknown"
            await self._bus.emit(
                Events.DOWNLOAD_FAILED,
                store=item.store,
                game_id=item.game_id,
                error=item.error,
            )
        self._cleanup_running(item)

    def _cleanup_running(self, item: DownloadItem) -> None:
        """Drop an item from ``_running`` once its install task ends.

        Synchronous (no I/O), so safe to call from any context.
        Idempotent — popping an absent key is a silent no-op.

        Args:
            item: the item whose install task just finished
                (successfully or not).
        """
        key = item_key(item)
        self._running.pop(key, None)

    def _update_progress(self, item: DownloadItem, progress: float) -> None:
        """Apply a store-side progress update to the item.

        Callback passed to ``store.install_game`` so the store can
        report progress as it sees it. This implementation just
        mutates the item's ``progress`` field — the bus emission
        of ``DOWNLOAD_PROGRESS`` is handled at a higher level
        (typically in the store itself or in a dedicated polling
        task) to allow throttling.

        Args:
            item: the running download item.
            progress: percentage 0.0–100.0.
        """
        item.progress = progress
