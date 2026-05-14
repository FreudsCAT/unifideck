"""Multi-store library sync orchestrator.

OP-08l | py_modules/unifideck/core/sync_service.py

``SyncService`` walks every registered store, calls its
``get_library`` method, deduplicates the combined output, and
emits bus events at every stage so the frontend can render a
progress bar + per-store status.

Lifecycle of one sync:

1. ``SYNC_STARTED``    — once at entry with the store list.
2. Per store:
   * ``SYNC_PROGRESS`` — i/N progress;
   * ``SYNC_FAILED``   — on per-store failure (other
     stores continue);
   * ``LAUNCHER_STAGE`` — user-facing toast with a retry
     action.
3. Dedup pass → emits ``SYNC_DEDUP`` if any duplicates were
   dropped.
4. ``SYNC_COMPLETE``   — once with the final unified list.

If cancelled mid-sync (``SYNC_CANCELLED``) the partial result
is preserved + returned so a follow-up sync resumes cleanly.

State retained across sync passes:

* ``_all_games``       — per-store deduplicated library;
* ``_last_sync_time``  — wall-clock timestamp;
* ``_current_store``   — for progress display;
* ``_lock``            — single-flight (only one sync at a
  time);
* ``_cancel_event``    — cooperative cancel signal.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from unifideck.event_bus import EventBus
from unifideck.steam.owned_games import get_owned_titles as _steam_owned_titles
from unifideck.stores import StoreRegistry

from .cross_store_dedup import deduplicate_libraries
from .types import Events, Game, SyncResult

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.stores.shared.store_base import StoreBase

logger = logging.getLogger(__name__)


class SyncService:
    """Single-flight multi-store library sync orchestrator."""

    def __init__(
        self,
        registry: StoreRegistry,
        bus: EventBus,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialise with the store registry + event bus + optional config.

        Lock + cancel event are constructed eagerly so
        ``sync_all`` can be called immediately after
        construction without an init pass.

        Args:
            registry: ``StoreRegistry`` to enumerate
                available stores.
            bus: event bus for status / progress events.
            config: optional ``ConfigManager`` (used to
                read tracked-stores list for dedup).
        """
        self._registry = registry
        self._bus = bus
        self._config = config
        self._lock = asyncio.Lock()
        self._cancel_event = asyncio.Event()
        self._all_games: dict[str, list[Game]] = {}
        self._last_sync_time: float | None = None
        self._current_store: str | None = None

    async def sync_all(self, *, force: bool = False) -> SyncResult:
        """Run a full multi-store sync, rejecting concurrent calls by default.

        Single-flight semantics: while a sync is in
        progress the lock is held and a second
        ``sync_all`` returns immediately with
        ``error="sync_already_running"``. ``force=True``
        bypasses the lock check — used by tests / admin
        actions; production callers should respect the
        single-flight rule.

        Args:
            force: bypass the concurrency check.

        Returns:
            ``SyncResult`` from the full sync, or an
            error result when rejected by the lock.
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
        """Core sync loop — emits events + handles cancellation.

        Walks every available store sequentially (not
        parallel, to avoid hammering the user's network
        and to keep progress reporting linear). After
        every store, checks the cancel flag and bails out
        early if set.

        Empty-store edge case: emits SYNC_COMPLETE with
        empty list rather than treating "no stores" as an
        error.

        Returns:
            ``SyncResult`` with the merged games list +
            timing.
        """
        self._cancel_event.clear()
        started = time.monotonic()
        available_stores = self._registry.available()
        total = len(available_stores)
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
                Events.SYNC_COMPLETE,
                games=[],
                stores_synced=[],
            )
            return SyncResult(
                success=True,
                games=[],
                count=0,
                duration_ms=0,
            )
        for idx, store in enumerate(available_stores):
            if self._cancel_event.is_set():
                logger.info(
                    "[SyncService] sync cancelled at store %d/%d",
                    idx,
                    total,
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
            libraries,
            errors,
            duration_ms,
            total,
        )
        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=result.games,
            stores_synced=list(libraries.keys()),
            errors=errors,
            duration_ms=duration_ms,
        )
        return result

    async def _apply_dedup_and_emit(
        self,
        libraries: dict[str, list[Game]],
    ) -> dict[str, list[Game]]:
        """Run cross-store dedup, emitting ``SYNC_DEDUP`` if anything was dropped.

        Delegates to
        ``cross_store_dedup.deduplicate_libraries``
        passing the configured tracked-stores list and
        the Steam-owned title set. Emits the dedup event
        only if at least one duplicate was actually
        dropped — keeps the event channel quiet for
        no-op sync cycles.

        Args:
            libraries: raw per-store library mapping.

        Returns:
            Deduplicated mapping (same shape).
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
        """Resolve the tracked-stores list for dedup priority.

        Reads ``dedup.tracked_stores`` from config; falls
        back to the four-store hardcoded default
        (``("epic", "gog", "amazon", "ubisoft")``) on:

        * No config supplied;
        * Config raises during ``get``;
        * Value isn't a list / tuple.

        Wrong-type values log at WARN so misconfigurations
        are visible.

        Returns:
            Tuple of store names, ordered by priority.
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

    async def sync_single_store(self, store_name: str) -> tuple[bool, str | None]:
        """Sync just one store and merge its result into the running library.

        Used by the ``refresh-library`` URI verb. Unlike
        ``sync_all``, doesn't hold the single-flight
        lock — the caller is responsible for not racing
        a full sync.

        After fetching the store's library, runs the
        full dedup pass over the merged state so
        cross-store consistency is preserved.

        Args:
            store_name: store id to refresh.

        Returns:
            ``(success_bool, optional_error_string)``.
        """
        store = self._registry.get_store(store_name)
        if store is None:
            logger.warning(
                "[SyncService] refresh-library: unknown store %r",
                store_name,
            )
            return False, "unknown_store"
        await self._bus.emit(
            Events.SYNC_STARTED,
            stores=[store_name],
            scope="single",
        )
        await self._emit_progress(store_name, 0, 1)
        games, err = await self._sync_one_store(store)
        if self._all_games is None:
            self._all_games = {}
        self._all_games[store_name] = games
        self._all_games = await self._apply_dedup_and_emit(self._all_games)
        self._last_sync_time = time.time()
        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=self._flatten(self._all_games),
            stores_synced=[store_name],
            errors={store_name: err} if err else {},
            duration_ms=0,
        )
        return err is None, err

    async def _sync_one_store(self, store: StoreBase) -> tuple[list[Game], str | None]:
        """Fetch one store's library, with broad exception handling.

        Success path: log the count + return the list.
        Failure path: catch any exception (broad
        ``Exception`` — store implementations may raise
        store-specific custom types), log the traceback,
        emit two events (``SYNC_FAILED`` for machinery +
        ``LAUNCHER_STAGE`` with a user-facing retry toast),
        return an empty list with the error string.

        The toast carries an ``unifideck://refresh-library/<store>``
        deep link as its action so the user can retry
        with one tap.

        Args:
            store: a ``StoreBase`` instance.

        Returns:
            ``(games_list, optional_error_string)``.
        """
        try:
            games = await store.get_library()
            if games is None:
                games = []
            logger.info(
                "[SyncService] %s: %d games",
                store.store_name,
                len(games),
            )
            return games, None
        except Exception as e:
            logger.exception("[SyncService] %s sync failed", store.store_name)
            await self._bus.emit(
                Events.SYNC_FAILED,
                store=store.store_name,
                error=str(e),
            )
            await self._bus.emit(
                Events.LAUNCHER_STAGE,
                severity="warning",
                i18n_key="toasts.library.syncStoreFailed",
                i18n_params={
                    "store": store.store_name,
                    "error": str(e)[:120],
                },
                duration_ms=8000,
                action={
                    "i18n_label_key": "toasts.actions.retryLibrarySync",
                    "target_url": (f"unifideck://refresh-library/{store.store_name}"),
                },
                store=store.store_name,
            )
            return [], str(e)

    async def _emit_progress(self, store_name: str, idx: int, total: int) -> None:
        """Emit ``SYNC_PROGRESS`` with the i/N progress payload.

        Progress is integer percent (0-100); guards
        against division-by-zero when ``total`` is 0
        (yields ``progress=0``).

        Args:
            store_name: store currently being processed.
            idx: zero-based index in the store list.
            total: total store count.
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
        """Build the final ``SyncResult`` + log the summary line.

        Partial-success heuristic: ``success=True`` if
        at least one store contributed (i.e.
        ``len(errors) < total_stores``). Surfacing
        partial failures via the error string lets the
        frontend show a per-store badge while still
        treating the overall sync as usable.

        Args:
            libraries: per-store deduplicated mapping.
            errors: per-store error strings.
            duration_ms: total elapsed time.
            total_stores: enumerable-store count from the
                registry (used in the success heuristic).

        Returns:
            ``SyncResult`` with merged games + counts.
        """
        merged = self._flatten(libraries)
        logger.info(
            "[SyncService] sync complete — %d games across %d stores "
            "in %dms (%d errors)",
            len(merged),
            len(libraries),
            duration_ms,
            len(errors),
        )
        return SyncResult(
            success=len(errors) < total_stores,
            games=merged,
            count=len(merged),
            duration_ms=duration_ms,
            error=None if not errors else f"{len(errors)}_stores_failed",
        )

    async def cancel(self) -> bool:
        """Request cancellation of the in-flight sync.

        Cooperative: the sync loop checks
        ``self._cancel_event`` between stores, so cancel
        takes effect at the next iteration (not
        mid-store). Returns ``False`` immediately if no
        sync is running.

        Returns:
            ``True`` if a sync was running and a cancel
            request was registered, ``False`` otherwise.
        """
        if not self._lock.locked():
            return False
        self._cancel_event.set()
        logger.info("[SyncService] cancel requested")
        return True

    def get_status(self) -> dict[str, Any]:
        """Return a JSON-friendly status snapshot.

        Used by the frontend's status poller. Cheap —
        all four fields are O(1) reads + one sum.

        Returns:
            Dict with ``syncing``, ``current_store``,
            ``last_sync_time``, ``total_games``.
        """
        return {
            "syncing": self._lock.locked(),
            "current_store": self._current_store,
            "last_sync_time": self._last_sync_time,
            "total_games": sum(len(g) for g in self._all_games.values()),
        }

    def get_all_games(self) -> list[Game]:
        """Return the merged unified library (flattened across stores).

        Snapshot copy via ``_flatten`` — caller can
        iterate without worrying about ``_all_games``
        being mutated by a concurrent sync.

        Returns:
            List of ``Game`` instances.
        """
        return self._flatten(self._all_games)

    def get_games_by_store(self, store: str) -> list[Game]:
        """Return the games list for one store (shallow copy).

        Empty list when the store has no games or
        doesn't exist — callers don't need to handle a
        ``None`` return.

        Args:
            store: store identifier.

        Returns:
            List of games (shallow copy).
        """
        return list(self._all_games.get(store, []))

    def get_game_info(self, app_id: int) -> dict[str, Any] | None:
        """Find a game by AppID and return its dict form.

        Linear scan across every store's list — O(N) on
        total library size. Acceptable because callers
        are interactive (single game per call from RPC),
        not bulk loops.

        ``dataclasses.asdict`` is imported lazily inside
        the hit branch to keep the cold path zero-cost
        (no import unless something matches).

        Args:
            app_id: Steam-style AppID.

        Returns:
            Dict form of the game, or ``None`` if not
            found.
        """
        for games in self._all_games.values():
            for game in games:
                if game.app_id == app_id:
                    from dataclasses import asdict

                    return asdict(game)
        return None

    @staticmethod
    def _flatten(libraries: dict[str, list[Game]]) -> list[Game]:
        """Merge per-store lists into one flat list.

        Order: dict-iteration order over stores (insertion
        order on CPython 3.7+), then per-store insertion
        order. Stable across calls so the UI's game
        ordering doesn't shuffle between syncs.

        Args:
            libraries: per-store mapping.

        Returns:
            Single flat list of games.
        """
        merged: list[Game] = []
        for games in libraries.values():
            merged.extend(games)
        return merged
