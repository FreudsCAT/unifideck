"""core/sync_service.py — Generic library synchronization orchestrator.

# OP-04d | core/sync_service.py | Depends: OP-47a, OP-47b

Replaces the legacy 623-line ``Plugin.sync_libraries()`` monolith
with a store-agnostic loop that iterates ``StoreRegistry.all()``
and calls ``store.get_library()`` polymorphically. One store's
failure doesn't block the others — errors are captured per-store
and surfaced via SYNC_FAILED + retry toast on LAUNCHER_STAGE.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..event_bus.event_bus import EventBus
from ..stores import StoreRegistry
from .types import Events, Game, SyncResult

if TYPE_CHECKING:
    from ..stores.shared.store_base import StoreBase

logger = logging.getLogger(__name__)


class SyncService:
    """Orchestrates library sync across all registered stores.

    Stateless with respect to store-specific logic — iterate,
    fetch polymorphically, merge, emit. One ``asyncio.Lock``
    prevents concurrent runs. Cancellation via ``asyncio.Event``
    checked between stores.
    """

    def __init__(self, registry: StoreRegistry, bus: EventBus) -> None:
        """Init registry ref, bus ref, the single-run lock, cancel
        event, and empty per-store library cache.
        """
        self._registry = registry
        self._bus = bus
        self._lock = asyncio.Lock()
        self._cancel_event = asyncio.Event()
        self._is_syncing = False
        self._current_store: str | None = None
        self._last_sync_time: float | None = None
        self._libraries: dict[str, list[Game]] = {}
        self._all_games: list[Game] = []

    async def sync_all(self, *, force: bool = False) -> SyncResult:
        """Fetch every registered store's library, merge, emit events.
        If a sync is already running and ``force`` is False, return
        ``SyncResult(success=False, error="sync_already_running")``.
        Acquires ``self._lock`` before delegating to ``_run_sync``.
        """
        if self._lock.locked() and not force:
            return SyncResult(success=False, error="sync_already_running")

        async with self._lock:
            self._is_syncing = True
            self._cancel_event.clear()
            try:
                return await self._run_sync()
            finally:
                self._is_syncing = False

    async def _run_sync(self) -> SyncResult:
        """Inner loop — assumes lock is held.
        Emits SYNC_STARTED, iterates stores with cancel checks between
        each, delegates to ``_sync_one_store``, aggregates, emits
        SYNC_COMPLETE. Updates ``self._all_games`` cache.
        """
        t0 = time.monotonic()
        await self._bus.emit(Events.SYNC_STARTED)

        stores = self._registry.all()
        total = len(stores)
        libraries: dict[str, list[Game]] = {}
        errors: dict[str, str] = {}

        for idx, store in enumerate(stores):
            # Check cancellation between stores
            if self._cancel_event.is_set():
                logger.info("[SyncService] Sync cancelled between stores")
                return SyncResult(
                    success=False, error="sync_cancelled",
                    games=self._flatten(libraries),
                    count=sum(len(g) for g in libraries.values()),
                )

            self._current_store = store.store_name
            await self._emit_progress(store.store_name, idx, total)

            games, err = await self._sync_one_store(store)
            if err is not None:
                errors[store.store_name] = err
            else:
                libraries[store.store_name] = games

        duration_ms = int((time.monotonic() - t0) * 1000)
        self._libraries = libraries
        self._all_games = self._flatten(libraries)
        self._last_sync_time = time.time()
        self._current_store = None

        result = self._aggregate_results(libraries, errors, duration_ms, total)
        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=result.games,
            count=result.count,
            duration_ms=result.duration_ms,
            stores_synced=len(libraries),
        )
        return result

    async def sync_single_store(
        self, store_name: str,
    ) -> tuple[bool, str | None]:
        """Refresh one store's library without the global lock.
        Used by ``unifideck://refresh-library/<store>`` toast retries.
        Updates cache in place for the targeted store only. Emits
        SYNC_COMPLETE scoped to the single store.
        """
        try:
            store = self._registry.get(store_name)
        except KeyError:
            return False, f"store '{store_name}' not registered"

        games, err = await self._sync_one_store(store)
        if err is not None:
            return False, err

        self._libraries[store_name] = games
        self._all_games = self._flatten(self._libraries)

        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=games,
            count=len(games),
            store=store_name,
        )
        return True, None

    async def _sync_one_store(
        self, store: StoreBase,
    ) -> tuple[list[Game], str | None]:
        """Fetch one store's library with full error isolation.
        On exception: log, emit SYNC_FAILED + LAUNCHER_STAGE toast
        with retry action, return ``([], str(exc))``. Never propagates.
        """
        store_name = store.store_name
        try:
            games = await store.get_library()
            if games is None:
                # API failure — treat as empty but not error (partial sync OK)
                logger.warning("[SyncService] %s returned None (API failure?)", store_name)
                return [], None
            logger.info("[SyncService] %s: fetched %d games", store_name, len(games))
            return games, None
        except Exception as exc:
            error_msg = str(exc)
            logger.error("[SyncService] %s failed: %s", store_name, error_msg)
            await self._bus.emit(
                Events.SYNC_FAILED,
                store=store_name,
                error=error_msg,
            )
            await self._bus.emit(
                Events.LAUNCHER_STAGE,
                store=store_name,
                action=f"unifideck://refresh-library/{store_name}",
                message=f"Sync failed for {store_name}",
            )
            return [], error_msg

    async def _emit_progress(
        self, store_name: str, idx: int, total: int,
    ) -> None:
        """Emit SYNC_PROGRESS with percentage = idx/total.
        Pure side-effect; no business logic.
        """
        pct = int((idx / total) * 100) if total > 0 else 0
        await self._bus.emit(
            Events.SYNC_PROGRESS,
            store=store_name,
            pct=pct,
            idx=idx,
            total=total,
        )

    def _aggregate_results(
        self,
        libraries: dict[str, list[Game]],
        errors: dict[str, str],
        duration_ms: int,
        total_stores: int,
    ) -> SyncResult:
        """Flatten per-store libraries into a single ``SyncResult``.
        Pure function. Partial success is allowed — the result is
        marked failed only if every store failed.
        """
        all_games = self._flatten(libraries)
        all_failed = len(errors) == total_stores and total_stores > 0
        error_summary = "; ".join(f"{s}: {e}" for s, e in errors.items()) if errors else None

        return SyncResult(
            success=not all_failed,
            error=error_summary,
            games=all_games,
            count=len(all_games),
            duration_ms=duration_ms,
        )

    async def cancel(self) -> bool:
        """Signal the current sync to stop between stores.
        Returns True if a sync was running, False otherwise.
        Does not interrupt an in-flight store fetch — that's the
        store's responsibility via its own timeout.
        """
        if not self._is_syncing:
            return False
        self._cancel_event.set()
        logger.info("[SyncService] Cancel requested")
        return True

    def get_status(self) -> dict[str, Any]:
        """Return ``{syncing, current_store, last_sync_time, total_games}``
        for the frontend progress bar.
        """
        return {
            "syncing": self._is_syncing,
            "current_store": self._current_store,
            "last_sync_time": self._last_sync_time,
            "total_games": len(self._all_games),
        }

    def get_all_games(self) -> list[Game]:
        """Return flat merged game list from the last sync.
        Empty list if no sync has run yet.
        """
        return list(self._all_games)

    def get_games_by_store(self, store: str) -> list[Game]:
        """Return games for a single store from last sync, or empty list."""
        return list(self._libraries.get(store, []))

    def get_game_info(self, app_id: int) -> dict[str, Any] | None:
        """Look up a game by Unifideck ``app_id``, return as dict.
        Scans cached libraries — no I/O. Returns None if unknown.
        """
        for game in self._all_games:
            if game.app_id == app_id:
                return dataclasses.asdict(game)
        return None

    @staticmethod
    def _flatten(libraries: dict[str, list[Game]]) -> list[Game]:
        """Merge a ``{store: games}`` dict into a single flat list."""
        return [g for games in libraries.values() for g in games]
