"""services/download/worker.py — Worker loop + install dispatch.

Queue consumer: polls pending queue, enforces concurrency cap,
dispatches each install to the right store via the registry,
emits ``DOWNLOAD_{STARTED,COMPLETE,FAILED}``. Mixin — only
touches host state, no I/O primitives of its own.

Refactor history (2026-05-14): ``_worker_loop`` was a single
async function at CC=16. The locked critical section, the
post-lock dispatch, and the error/cancel envelope were all
inlined, making the main loop hard to scan. Split into two
private helpers so the outer loop reads as
``while: pop → dispatch → sleep``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .models import DownloadItem, classify_download_error

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)

# Strong references to background install tasks so the GC can't
# collect them mid-flight (see RUF006).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _track(task: asyncio.Task[Any]) -> None:
    """Register a fire-and-forget task so the GC doesn't collect it early."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# Polling cadence — kept as module constants so a future test
# can monkeypatch them to speed up integration runs without
# touching the loop logic itself.
_POLL_INTERVAL_SEC: float = 1.0
_ERROR_BACKOFF_SEC: float = 5.0


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
        """Poll the queue and dispatch installs until cancelled.

        Each iteration: pop items eligible to start under the
        lock, dispatch them outside the lock (so the queue save
        and ``create_task`` don't block other producers), then
        sleep. Cancellation and unexpected errors are handled
        as flat branches at the top level.
        """
        while True:
            try:
                to_start = await self._pop_ready_items()
                if to_start:
                    await self._dispatch_items(to_start)
                await asyncio.sleep(_POLL_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                # Any unexpected error: log with full traceback,
                # back off harder than the regular poll so we
                # don't burn CPU if the cause is persistent.
                logger.exception(
                    "[DownloadWorker] unhandled error in loop",
                )
                await asyncio.sleep(_ERROR_BACKOFF_SEC)

    async def _pop_ready_items(self) -> list[DownloadItem]:
        """Pop queue items eligible to start under the worker lock.

        Holds ``self._lock`` while mutating both ``self._queue``
        and ``self._running`` so concurrent producers (queue
        adders) see a consistent state. Returns the popped items
        for the caller to dispatch *outside* the lock — keeps
        the critical section as short as possible.
        """
        to_start: list[DownloadItem] = []
        async with self._lock:
            while (
                len(self._running) < self._max_concurrent
                and self._queue
            ):
                item = self._queue.pop(0)
                key = f"{item.store}:{item.game_id}"
                self._running[key] = item
                to_start.append(item)
        return to_start

    async def _dispatch_items(
        self, to_start: list[DownloadItem],
    ) -> None:
        """Persist the queue change, then spawn install tasks.

        Persistence runs first so a crash between pop and
        spawn doesn't resurrect items we've already committed
        to running. ``_save_queue`` is looked up dynamically:
        the queue-persistence mixin is optional, so the host
        class may or may not provide it.
        """
        save_method = getattr(self, "_save_queue", None)
        if callable(save_method):
            await save_method()
        for item in to_start:
            _track(asyncio.create_task(self._run_install(item)))

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
        from unifideck.core.types.events import Events

        try:
            # ``StoreRegistry.get_store`` returns ``None`` for unknown
            # stores; the sister ``get`` raises ``KeyError`` instead
            # which makes the ``if not store`` check below dead code
            # and lets a cryptic KeyError leak into the
            # DOWNLOAD_FAILED event payload. We want a clean
            # "store not found" classification.
            store = self._registry.get_store(item.store)
            if not store:
                raise RuntimeError(f"Store {item.store} not found in registry")

            if self._bus:
                await self._bus.emit(Events.DOWNLOAD_STARTED, item=item.to_dict())

            # Progress callback wrapper. Must be ``async`` to
            # match the contract every store expects — they call
            # ``await progress_cb(progress_dict)`` inside their
            # install loop. An earlier version defined this as a
            # sync ``def`` that returned ``None``; the stores'
            # ``await`` then raised ``TypeError: object NoneType
            # can't be used in 'await' expression`` on every
            # progress tick (silently caught by the stores'
            # broad-except). The store-side ``DOWNLOAD_PROGRESS``
            # emit still fired so progress made it to the UI, but
            # the worker's own emit was lost and the wrapper's
            # main job (updating ``item.progress``) was best-effort.
            async def progress_cb(progress_dict: dict[str, Any]) -> None:
                await self._update_progress(item, progress_dict)

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
                    await self._bus.emit(Events.DOWNLOAD_COMPLETE, item=item.to_dict())
            else:
                error_type = classify_download_error(result.error)
                logger.error("[DownloadWorker] failed install for %s: %s (%s)", key, result.error, error_type)
                if self._bus:
                    await self._bus.emit(Events.DOWNLOAD_FAILED, item=item.to_dict(), error=result.error, error_type=error_type)

        except Exception as e:
            error_type = classify_download_error(str(e))
            logger.exception("[DownloadWorker] exception during install of %s", key)
            if self._bus:
                await self._bus.emit(Events.DOWNLOAD_FAILED, item=item.to_dict(), error=str(e), error_type=error_type)
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

    async def _update_progress(self, item: DownloadItem, progress: dict[str, Any]) -> None:
        """Progress callback invoked from the store's ``install_game``.

        Store progress on the item, emit DOWNLOAD_PROGRESS.

        ``EventBus.emit`` is ``async`` — without ``await`` the
        returned coroutine is discarded and the event never
        reaches any subscriber, so this method must be ``async``
        and every call site (the ``progress_cb`` wrapper above)
        must ``await`` it.
        """
        item.progress = progress
        if self._bus:
            from unifideck.core.types.events import Events
            # We don't emit the full item dict on every progress tick to save IPC overhead,
            # just the identifiers and the progress dict.
            await self._bus.emit(
                Events.DOWNLOAD_PROGRESS,
                store=item.store,
                game_id=item.game_id,
                progress=progress,
            )
