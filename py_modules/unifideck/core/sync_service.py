"""core/sync_service.py — Generic library synchronization orchestrator.
Replaces the legacy 623-line `Plugin.sync_libraries()` monolith in
main.py which contained 5 hardcoded branches (one per store), 5 null
checks, 5 install-status loops and 5 games_map updates — adding a
6th store required duplicating code in every phase.
In the new architecture:
1. SyncService iterates `StoreRegistry.all()` — zero hardcoded branches
2. Each store is fetched via polymorphic `store.get_library()` call
3. Results are merged into a dict keyed by store name
4. SYNC_PROGRESS is emitted per store so the frontend can update a
 progress bar without knowing which stores are registered
5. SYNC_COMPLETE is emitted once with the full merged result
6. Errors are isolated per store: one failing store does not block
 the others; a STORE_ERROR event is emitted with the typed error
Concurrency model:
- A single `asyncio.Lock` prevents concurrent sync runs (the legacy
 behaviour: trying to start a sync while one is already running
 returns an error immediately)
- Cancellation is supported via an `asyncio.Event` that handlers check
 between stores; `cancel()` sets it and returns immediately
- Metadata and artwork services subscribe to SYNC_COMPLETE and run in
 parallel after sync finishes (via EventBus fan-out)
Reference: Technical Document v1.0 — Sections 3.4.4 (623L → 20L),
5.5 (sync flow comparison), Figure 52/53.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..event_bus import EventBus

# StoreRegistry moved from core/ to unifideck.stores
from ..stores import StoreRegistry
from .cross_store_dedup import deduplicate_libraries
from ..steam.owned_games import get_owned_titles as _steam_owned_titles
from .types import Events, Game, SyncResult

if TYPE_CHECKING:
    from ..config import ConfigManager
    from ..stores.shared.store_base import StoreBase

logger = logging.getLogger(__name__)
class SyncService:
    """Orchestrates library synchronization across all registered stores.
    The service is stateless with respect to store-specific logic: it
    knows how to iterate the registry, call get_library() polymorphically,
    merge results, and emit events. All store-specific behaviour lives
    in the StoreBase subclasses.
    Usage:
    registry = StoreRegistry()
    bus = EventBus()
    sync = SyncService(registry, bus)
    # Trigger a sync (usually from an RPC handler)
    result = await sync.sync_all()
    # Or subscribe to completion events
    bus.on(Events.SYNC_COMPLETE, on_sync_complete).
    """

    def __init__(
        self,
        registry: StoreRegistry,
        bus: EventBus,
        config: ConfigManager | None = None,
    ) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._registry = registry
        self._bus = bus
        self._config = config
        # Only one sync at a time — concurrent calls return immediately
        self._lock = asyncio.Lock()
        # Cancellation flag checked between stores
        self._cancel_event = asyncio.Event()
        # In-memory cache of the last merged library (used by RPC
        # methods that want to return the current library without
        # triggering a new fetch)
        self._all_games: dict[str, list[Game]] = {}
        self._last_sync_time: float | None = None
        self._current_store: str | None = None
        # ── Public API ──────────────────────────────────────────────
    async def sync_all(self, *, force: bool = False) -> SyncResult:
        """Fetch libraries from every available store, merge and emit.

        Args:
        force: If True, bypass the "already syncing" guard by
        waiting for the lock. If False and a sync is already
        running, returns a SYNC_FAILED result immediately.
        Returns a SyncResult with the merged game list and per-store
        counts. Even if some stores fail, the result contains the
        games from the stores that succeeded.

        """
        if self._lock.locked() and not force:
            logger.warning(
                "[SyncService] sync_all() called while "
                "another sync is running — rejected",
            )
            return SyncResult(
                success=False,
                error="sync_already_running",
                games=[],
            )
        async with self._lock:
            return await self._run_sync()
    async def _run_sync(self) -> SyncResult:
        """Internal sync loop — assumes the lock is already held.

        Uses three helpers so the loop stays testable:
        ``_sync_one_store`` (fetch with error isolation),
        ``_emit_progress`` (percentage event), and
        ``_aggregate_results`` (flatten + build SyncResult).
        """
        self._cancel_event.clear()
        started = time.monotonic()
        available_stores = self._registry.available()
        total = len(available_stores)
        # SYNC_STARTED triggers the frontend's toast confirmation,
        # decoupled from whichever code path called this sync.
        store_names = [s.store_name for s in available_stores]
        await self._bus.emit(
            Events.SYNC_STARTED,
            stores=store_names,
            scope="all",
        )
        logger.info("[SyncService] sync starting (%d stores)", total)

        libraries: dict[str, list[Game]] = {}
        errors: dict[str, str] = {}

        if total == 0:
            logger.warning(
                "[SyncService] no available stores — nothing to sync",
            )
            await self._bus.emit(
                Events.SYNC_COMPLETE, games=[], stores_synced=[],
            )
            return SyncResult(
                success=True, games=[], count=0, duration_ms=0,
            )

        for idx, store in enumerate(available_stores):
            if self._cancel_event.is_set():
                logger.info(
                    "[SyncService] sync cancelled at store %d/%d",
                    idx, total,
                )
                await self._bus.emit(Events.SYNC_CANCELLED)
                return SyncResult(
                    success=False,
                    error="cancelled",
                    games=self._flatten(libraries),
                )

            self._current_store = store.store_name
            await self._emit_progress(store.store_name, idx, total)

            games, err = await self._sync_one_store(store)
            libraries[store.store_name] = games
            if err is not None:
                errors[store.store_name] = err

        self._current_store = None
        duration_ms = int((time.monotonic() - started) * 1000)

        libraries = await self._apply_dedup_and_emit(libraries)

        self._all_games = libraries
        self._last_sync_time = time.time()

        result = self._aggregate_results(
            libraries, errors, duration_ms, total,
        )

        # Fan-out to subscribers (metadata, artwork, shortcuts...)
        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=result.games,
            stores_synced=list(libraries.keys()),
            errors=errors,
            duration_ms=duration_ms,
        )
        return result

    async def _apply_dedup_and_emit(
        self, libraries: dict[str, list[Game]],
    ) -> dict[str, list[Game]]:
        """Run the dedup pipeline and emit SYNC_DEDUP if anything dropped.

        Two filters in order (see ``core/cross_store_dedup.py``),
        both scoped to ``config.dedup.tracked_stores``:
          1. Steam-native ownership filter — drops titles the user
             already has on Steam from tracked stores only.
          2. Cross-store filter — keeps one copy across tracked
             stores in priority order.

        Untracked stores (typically Microsoft xCloud / Game Pass)
        pass through both filters unchanged.

        ``_steam_owned_titles()`` is called with the same config so
        ``paths.steam_candidates`` overrides are honoured.

        Returns the deduped libraries. SYNC_DEDUP is emitted only
        when at least one game was dropped — no point waking
        subscribers for a no-op cycle.
        """
        tracked = self._tracked_stores()
        steam_owned = _steam_owned_titles(self._config)
        deduped, dropped_per_store = deduplicate_libraries(
            libraries,
            tracked_stores=tracked,
            steam_owned_titles=steam_owned,
        )

        total_dropped = sum(dropped_per_store.values())
        if total_dropped:
            await self._bus.emit(
                Events.SYNC_DEDUP,
                total_removed=total_dropped,
                per_store=dict(dropped_per_store),
            )
        return deduped

    def _tracked_stores(self) -> tuple[str, ...]:
        """Return the configured tracked-stores tuple for dedup.

        Reads ``config.dedup.tracked_stores`` via the standard
        None-safe accessor. When SyncService was constructed
        without a ConfigManager (e.g. some unit tests), falls back
        to the same default list shipped in ``defaults/config.json``
        — so behaviour stays consistent with production.
        """
        default = ("epic", "gog", "amazon", "ubisoft")
        if self._config is None:
            return default
        try:
            value = self._config.get("dedup.tracked_stores", list(default))
        except Exception:
            return default
        if not isinstance(value, (list, tuple)):
            logger.warning(
                "[SyncService] dedup.tracked_stores has wrong type "
                "(%s); falling back to defaults",
                type(value).__name__,
            )
            return default
        return tuple(value)

    async def sync_single_store(
    self, store_name: str,
    ) -> tuple[bool, str | None]:
        """Re-sync the library for exactly one store.

        Used by the ``unifideck://refresh-library/<store>`` toast
        action verb when the user retries a failed sync from a
        toast notification. Unlike ``sync_all()``, this method:

          - **Doesn't lock out** other syncs — it's a surgical
            refresh of a single store, expected to run
            concurrently with other background activity.
          - **Updates the cache in place** for the targeted store
            only — other stores keep their last-known library.
          - **Emits SYNC_PROGRESS + SYNC_COMPLETE** with only the
            refreshed store so the Library view can update its
            inline state without refetching everything.

        Returns (success, error_or_none). On failure, the toast
        helpers in ``_sync_one_store`` have already emitted a new
        LAUNCHER_STAGE toast with its own retry button — so a
        second failure becomes another click opportunity rather
        than silent dead end.
        """
        store = self._registry.get_store(store_name)
        if store is None:
            logger.warning(
                "[SyncService] refresh-library: unknown store %r",
                store_name,
            )
            return False, "unknown_store"

        # Emit progress so the Library view shows a spinner
        # for this store. idx/total are 0/1 since we're only
        # touching one store in this call.
        await self._bus.emit(
            Events.SYNC_STARTED,
            stores=[store_name],
            scope="single",
        )
        await self._emit_progress(store_name, 0, 1)

        games, err = await self._sync_one_store(store)

        # Merge into the existing cache in place. Other
        # stores' games are preserved.
        if self._all_games is None:
            self._all_games = {}
        self._all_games[store_name] = games

        # Re-run dedup against the full merged cache (idempotent —
        # both filters re-running on a clean cache are a no-op).
        # The helper also emits SYNC_DEDUP when relevant.
        self._all_games = await self._apply_dedup_and_emit(self._all_games)
        self._last_sync_time = time.time()

        # Emit SYNC_COMPLETE scoped to just this store, so UI
        # consumers can refresh the affected rows without
        # rebuilding their entire game list.
        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=self._flatten(self._all_games),
            stores_synced=[store_name],
            errors={store_name: err} if err else {},
            duration_ms=0,  # single-store refresh — not timed
        )
        return err is None, err

    async def _sync_one_store(
    self, store: StoreBase,
    ) -> tuple[list[Game], str | None]:
        """Fetch one store's library with full error isolation.

        Returns a tuple `(games, error_or_none)`. If the store raises
        during `get_library()`:
          - the exception is logged at ERROR with full traceback
          - a SYNC_FAILED event is emitted with the error text
          - we return `([], "<error text>")` so the sync can continue
            with other stores

        This is the **only** place in the sync loop where an exception
        is caught. Callers can rely on the invariant that this method
        never propagates a store-side error.
        """
        try:
            games = await store.get_library()
            if games is None:
                games = []
            logger.info(
                "[SyncService] %s: %d games",
                store.store_name, len(games),
            )
            return games, None
        except Exception as e:
            logger.exception(
                "[SyncService] %s sync failed: %s",
                store.store_name, e,
            )
            # Legacy event — kept for other consumers that already
            # listen on SYNC_FAILED (metrics collector, UI library
            # view's inline error message). Do not alter its payload
            # shape; downstream code depends on the (store, error)
            # contract.
            await self._bus.emit(
                Events.SYNC_FAILED,
                store=store.store_name,
                error=str(e),
            )
            # Separately, emit a toast-formatted event on the
            # LAUNCHER_STAGE channel so the LauncherToastListener
            # surfaces a notification with a one-click retry. Uses
            # the unifideck://refresh-library/{store} URI scheme:
            # the user clicks, the backend re-attempts the sync for
            # that single store in the background (fire-and-forget
            # from the user's perspective — the Library view will
            # refresh via its own event stream when the sync
            # completes). Kept as a separate emit to preserve the
            # single-responsibility of SYNC_FAILED.
            await self._bus.emit(
                Events.LAUNCHER_STAGE,
                severity="warning",
                i18n_key="toasts.library.syncStoreFailed",
                i18n_params={
                    "store": store.store_name,
                    "error": str(e)[:120],  # truncate long stacks
                },
                duration_ms=8000,  # longer: library sync is less
                                   # frequent than cloud sync, so
                                   # give the user time to click
                action={
                    "i18n_label_key": "toasts.actions.retryLibrarySync",
                    "target_url": (
                        f"unifideck://refresh-library/{store.store_name}"
                    ),
                },
                store=store.store_name,
            )
            return [], str(e)

    async def _emit_progress(
    self, store_name: str, idx: int, total: int,
    ) -> None:
        """Emit SYNC_PROGRESS with a computed percentage.

        Pure side effect — no business logic. The percentage is the
        fraction of stores already processed (not the current one),
        so a 5-store sync emits 0%, 20%, 40%, 60%, 80% as each store
        begins. The final 100% is emitted by SYNC_COMPLETE.
        """
        await self._bus.emit(
            Events.SYNC_PROGRESS,
            store=store_name,
            progress=int((idx / total) * 100) if total else 0,
            current=idx + 1,
            total=total,
        )

    def _aggregate_results(
    self,
    libraries: dict[str, list[Game]],
    errors: dict[str, str],
    duration_ms: int,
    total_stores: int,
    ) -> SyncResult:
        """Flatten per-store libraries and build the final SyncResult.

        Pure function: no I/O, no event emission, no state mutation.
        The caller decides what to do with the result (emit, return,
        persist). Keeping this pure lets tests verify aggregation
        logic in isolation without mocking bus/registry.

        Success semantics: partial success is allowed. The sync is
        considered successful as long as at least one store returned
        without error. Only if every store failed do we mark the
        overall result as a failure.
        """
        merged = self._flatten(libraries)
        logger.info(
            "[SyncService] sync complete — %d games across %d stores "
            "in %dms (%d errors)",
            len(merged), len(libraries), duration_ms, len(errors),
        )
        return SyncResult(
            success=len(errors) < total_stores,
            games=merged,
            count=len(merged),
            duration_ms=duration_ms,
            error=None if not errors else f"{len(errors)}_stores_failed",
        )

    async def cancel(self) -> bool:
        """Signal the current sync to stop.
        Sets the cancellation event; the sync loop will stop between
        stores (not mid-fetch — cancelling a CLI call or HTTP request
        is the store's responsibility). Returns True if a sync was
        running, False otherwise.
        """
        if not self._lock.locked():
            return False
        self._cancel_event.set()
        logger.info("[SyncService] cancel requested")
        return True
            # ── Query API (no side effects) ────────────────────────────
    def get_status(self) -> dict[str, Any]:
        """Return current sync status for the frontend progress bar."""
        return {
        "syncing": self._lock.locked(),
        "current_store": self._current_store,
        "last_sync_time": self._last_sync_time,
        "total_games": sum(
        len(g) for g in self._all_games.values()
        ),
        }
    def get_all_games(self) -> list[Game]:
        """Return the flat merged list of games from the last sync.
        Returns an empty list if no sync has run yet. Used by RPC
        methods that need to show the library without triggering a
        New fetch.
        """
        return self._flatten(self._all_games)

    def get_games_by_store(self, store: str) -> list[Game]:
        """Return games for a single store from the last sync."""
        return list(self._all_games.get(store, []))

    def get_game_info(self, app_id: int) -> dict[str, Any] | None:
        """Look up a single game by its Unifideck app_id.

        Scans the cached libraries from the last sync — no I/O.
        Returns ``None`` if the app_id isn't known, so the RPC
        handler can translate that to a clean 404 for the
        frontend rather than an exception.
        """
        for games in self._all_games.values():
            for game in games:
                if game.app_id == app_id:
                    # Return a plain dict for RPC serialization.
                    from dataclasses import asdict
                    return asdict(game)
        return None
        # ── Internals ───────────────────────────────────────────────
    @staticmethod
    def _flatten(libraries: dict[str, list[Game]]) -> list[Game]:
        """Merge a {store: games} dict into a single flat list."""
        merged: list[Game] = []
        for games in libraries.values():
            merged.extend(games)
        return merged
