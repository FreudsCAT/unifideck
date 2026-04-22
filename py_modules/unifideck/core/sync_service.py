"""core/sync_service.py — Generic library synchronization orchestrator.

# OP-04d | core/sync_service.py | Depends: OP-47a, OP-47b

Replaces the legacy 623-line ``Plugin.sync_libraries()`` monolith
with a store-agnostic loop that iterates ``StoreRegistry.all()``
and calls ``store.get_library()`` polymorphically. One store's
failure doesn't block the others — errors are captured per-store
and surfaced via SYNC_FAILED + retry toast on LAUNCHER_STAGE.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..event_bus.event_bus import EventBus
from ..stores import StoreRegistry
from .types import Game, SyncResult

if TYPE_CHECKING:
    from ..stores.shared.store_base import StoreBase


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
        raise NotImplementedError("OP-04d: init lock, cancel event, empty caches")

    async def sync_all(self, *, force: bool = False) -> SyncResult:
        """Fetch every registered store's library, merge, emit events.
        If a sync is already running and ``force`` is False, return
        ``SyncResult(success=False, error="sync_already_running")``.
        Acquires ``self._lock`` before delegating to ``_run_sync``.
        """
        raise NotImplementedError("OP-04d: acquire lock, delegate to _run_sync")

    async def _run_sync(self) -> SyncResult:
        """Inner loop — assumes lock is held.
        Emits SYNC_STARTED, iterates stores with cancel checks between
        each, delegates to ``_sync_one_store``, aggregates, emits
        SYNC_COMPLETE. Updates ``self._all_games`` cache.
        """
        raise NotImplementedError("OP-04d: implement store iteration loop")

    async def sync_single_store(
        self, store_name: str,
    ) -> list[Game] | None:
        """Refresh one store's library without the global lock.
        Used by ``unifideck://refresh-library/<store>`` toast retries.
        Updates cache in place for the targeted store only. Emits
        SYNC_COMPLETE scoped to the single store.
        """
        raise NotImplementedError("OP-04d: fetch one store, update partial cache")

    async def _sync_one_store(
        self, store: StoreBase,
    ) -> tuple[list[Game], str | None]:
        """Fetch one store's library with full error isolation.
        On exception: log, emit SYNC_FAILED + LAUNCHER_STAGE toast
        with retry action, return ``([], str(exc))``. Never propagates.
        """
        raise NotImplementedError("OP-04d: try store.get_library(), isolate errors")

    async def _emit_progress(
        self, store_name: str, idx: int, total: int,
    ) -> None:
        """Emit SYNC_PROGRESS with percentage = idx/total.
        Pure side-effect; no business logic.
        """
        raise NotImplementedError("OP-04d: bus.emit(SYNC_PROGRESS, ...)")

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
        raise NotImplementedError("OP-04d: merge libraries, build SyncResult")

    async def cancel(self) -> bool:
        """Signal the current sync to stop between stores.
        Returns True if a sync was running, False otherwise.
        Does not interrupt an in-flight store fetch — that's the
        store's responsibility via its own timeout.
        """
        raise NotImplementedError("OP-04d: set cancel event, return was_running")

    def get_status(self) -> dict[str, Any]:
        """Return ``{syncing, current_store, last_sync_time, total_games}``
        for the frontend progress bar.
        """
        raise NotImplementedError("OP-04d: return status dict")

    def get_all_games(self) -> list[Game]:
        """Return flat merged game list from the last sync.
        Empty list if no sync has run yet.
        """
        raise NotImplementedError("OP-04d: return self._all_games")

    def get_games_by_store(self, store: str) -> list[Game]:
        """Return games for a single store from last sync, or empty list."""
        raise NotImplementedError("OP-04d: return self._libraries.get(store, [])")

    def get_game_info(self, app_id: int) -> dict[str, Any] | None:
        """Look up a game by Unifideck ``app_id``, return as dict.
        Scans cached libraries — no I/O. Returns None if unknown.
        """
        raise NotImplementedError("OP-04d: scan _all_games for matching app_id")

    @staticmethod
    def _flatten(libraries: dict[str, list[Game]]) -> list[Game]:
        """Merge a ``{store: games}`` dict into a single flat list."""
        raise NotImplementedError("OP-04d: [g for games in libraries.values() for g in games]")
