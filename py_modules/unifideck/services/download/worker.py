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
import time
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
            except Exception:
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
        dispatch to the correct store with per-store argument
        conventions, classify the result (``InstallResult``) or
        any exception via ``classify_download_error``, emit
        DOWNLOAD_COMPLETE or DOWNLOAD_FAILED with the classified
        error, always ``_cleanup_running(item)`` in a finally
        block.
        """
        key = f"{item.store}:{item.game_id}"
        from unifideck.core.types.events import Events

        try:
            store = self._registry.get_store(item.store)
            if not store:
                raise RuntimeError(f"Store {item.store} not found in registry")

            # Microsoft/xCloud games are streamed, not downloaded
            if item.store == "microsoft":
                logger.warning("[DownloadWorker] Microsoft games are cloud-only, cannot download")
                if self._bus:
                    await self._bus.emit(
                        Events.DOWNLOAD_FAILED,
                        item=item.to_dict(),
                        error="Microsoft games are streamed via Xbox Cloud Gaming",
                        error_type="cloud_only",
                    )
                return

            if self._bus:
                await self._bus.emit(Events.DOWNLOAD_STARTED, item=item.to_dict())

            # Progress callback wrapper — handles both float (Epic/Amazon
            # emit bare percentages) and dict (GOG/Ubisoft emit structured
            # progress payloads).
            async def progress_cb(progress: Any) -> None:
                await self._update_progress(item, progress)

            # Per-store dispatch — each store has a different signature:
            #   Epic/Amazon: install_game(game_id, base_path, progress_cb)
            #   GOG:         install_game(game_id, base_path, progress_cb, language?)
            #   Ubisoft:     install_game(game_id, *, progress_cb, install_path)
            logger.info("[DownloadWorker] starting install for %s", key)
            if item.store == "ubisoft":
                result = await store.install_game(  # type: ignore[call-arg]
                    item.game_id,
                    progress_cb=progress_cb,
                    install_path=item.install_path or None,
                )
            else:
                # Epic, Amazon, GOG — all accept base_path as positional #2
                result = await store.install_game(  # type: ignore[call-arg]
                    item.game_id,
                    item.install_path or None,
                    progress_cb=progress_cb,
                )

            if result.success:
                item.progress = 100.0
                item.status = "complete"
                item.end_time = time.time()
                if getattr(result, "install_path", None):
                    item.install_path = result.install_path
                logger.info("[DownloadWorker] completed install for %s", key)
                if self._bus:
                    await self._bus.emit(Events.DOWNLOAD_COMPLETE, item=item.to_dict())
                # Invoke post-install callback (writes marker, registers game)
                on_complete = getattr(self, "_on_complete_callback", None)
                if callable(on_complete):
                    try:
                        await on_complete(item)
                    except Exception as exc:
                        logger.exception("[DownloadWorker] on_complete callback failed: %s", exc)
            else:
                error_type = classify_download_error(result.error or "")  # type: ignore[arg-type]
                logger.error("[DownloadWorker] failed install for %s: %s (%s)", key, result.error, error_type)
                if self._bus:
                    await self._bus.emit(Events.DOWNLOAD_FAILED, item=item.to_dict(), error=result.error, error_type=error_type)

        except Exception as e:
            error_type = classify_download_error(str(e))  # type: ignore[arg-type]
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

    async def _update_progress(self, item: DownloadItem, progress: Any) -> None:
        """Progress callback invoked from the store's ``install_game``.

        Stores emit progress in two shapes:
        - Epic/Amazon pass a bare ``float`` (0.0-100.0).
        - GOG/Ubisoft pass a ``dict`` with ``percentage``,
          ``downloaded_bytes``, ``total_bytes``, ``speed_bps``,
          ``eta_seconds``, ``phase``, ``phase_message``.
        """
        if isinstance(progress, (int, float)):
            item.progress = float(progress)
            if item.progress > 0:
                item.download_phase = "downloading"
        elif isinstance(progress, dict):
            pct = progress.get("percentage") or progress.get("progress_percent")
            if isinstance(pct, (int, float)):
                item.progress = float(pct)
            if "downloaded_bytes" in progress:
                item.downloaded_bytes = int(progress["downloaded_bytes"])
            if "total_bytes" in progress:
                item.total_bytes = int(progress["total_bytes"])
            if "speed_mbps" in progress:
                item.speed_mbps = float(progress["speed_mbps"])
            elif "speed_bps" in progress:
                item.speed_mbps = float(progress["speed_bps"]) / (1024 * 1024)
            if "eta_seconds" in progress:
                item.eta_seconds = int(progress["eta_seconds"])
            if "phase" in progress:
                item.download_phase = str(progress["phase"])
            if "phase_message" in progress:
                item.phase_message = str(progress["phase_message"])
        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(
                Events.DOWNLOAD_PROGRESS,
                store=item.store,
                game_id=item.game_id,
                progress=item.progress,
                speed_mbps=item.speed_mbps,
                eta_seconds=item.eta_seconds,
            )
