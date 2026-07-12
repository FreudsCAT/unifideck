"""Post-sync finalize mixin for :class:`SyncService`.

Extracted from ``core/sync_run_mixin.py`` (2026-07-12, UD-006) to keep
that file under the 550-LOC volumetry cap. Owns the tail of a sync run:
persisting state, arming the post-sync phase set + watchdog, and emitting
SYNC_COMPLETE / LIBRARY_SYNC_COMPLETED.

The per-run fetch loop, setup, and cancellation handling stay in
``_SyncRunMixin``; construction and bus handlers stay in ``SyncService``.
All consumed state + sibling-mixin methods are declared as
``TYPE_CHECKING`` annotations — the host provides them at runtime.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .types import Events, Game, SyncResult

if TYPE_CHECKING:
    from unifideck.core.sync_progress import SyncProgress
    from unifideck.event_bus import EventBus
    from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)

# Watchdog timeout for post-sync hooks. Real syncs of 1000+ games
# spend 15-30 minutes in the metadata-enrichment phase, so this is
# generous on purpose — it only catches pathological stuck states
# (bus.emit raises, task is killed without reaching its finally).
POST_SYNC_WATCHDOG_SECONDS = 1800


class _SyncFinalizeMixin:
    """Post-sync finalization for :class:`SyncService`."""

    # State provided by the host SyncService at runtime.
    _registry: StoreRegistry
    _bus: EventBus
    _all_games: dict[str, list[Game]]
    _last_sync_time: float | None
    _progress: SyncProgress
    _post_sync_pending: set[str]
    _registered_phases: set[str]
    _watchdog_task: asyncio.Task[None] | None
    _cache_snapshot: dict[str, dict[str, Any]] | None

    if TYPE_CHECKING:
        # Sibling-mixin methods composed onto the host SyncService.
        def _save_library_cache(self) -> None: ...
        def _populate_app_ids(
            self, libraries: dict[str, list[Game]],
        ) -> None: ...
        def _aggregate_results(
            self,
            libraries: dict[str, list[Game]],
            errors: dict[str, str],
            duration_ms: int,
            total: int,
        ) -> SyncResult: ...

    async def _finalize_sync(
        self,
        libraries: dict[str, list[Game]],
        errors: dict[str, str],
        total: int,
        started: float,
        *,
        fetch_artwork: bool = True,
        resync_artwork: bool = False,
        is_force: bool = False,
    ) -> SyncResult:
        """Compute duration, dedup, persist state, emit SYNC_COMPLETE.

        Args:
            fetch_artwork: when ``False``, mark the artwork phase as
                already-done in ``_post_sync_pending`` so the progress
                bar doesn't stall at 60%.
            resync_artwork: forwarded to ArtworkService via the
                SYNC_COMPLETE payload; treated as ``force``.
            is_force: forwarded so ShortcutService UPDATEs (not just
                KEEPs) existing shortcuts.

        Side effects: updates ``self._all_games`` and
        ``self._last_sync_time``.
        """
        duration_ms = int((time.monotonic() - started) * 1000)
        self._populate_app_ids(libraries)
        self._all_games = libraries
        total_games = sum(len(g) for g in libraries.values())
        self._progress.set_library_totals(total_games)
        self._arm_artwork_phase(fetch_artwork, total_games)
        self._last_sync_time = time.time()
        self._save_library_cache()
        self._arm_watchdog()
        result = self._aggregate_results(libraries, errors, duration_ms, total)
        await self._emit_complete(
            result, libraries, errors, duration_ms, total_games,
            fetch_artwork=fetch_artwork,
            resync_artwork=resync_artwork,
            is_force=is_force,
        )
        # Successful finalize — release the snapshot so the GC can
        # reclaim it before the post-sync phases fill caches afresh.
        self._cache_snapshot = None
        return result

    def _arm_artwork_phase(self, fetch_artwork: bool, total_games: int) -> None:
        """Seed ``_post_sync_pending``; start the artwork phase if fetching.

        ``resync_artwork`` / ``fetch_artwork`` themselves are forwarded
        to ArtworkService via the SYNC_COMPLETE payload (it owns the
        SGDB failure-cooldown cache). Signalling the phase here, before
        the emit, lets the frontend's polling loop see the transition.
        """
        self._post_sync_pending = set(self._registered_phases)
        if fetch_artwork:
            self._progress.start_artwork(total_games)
        else:
            # Skip the artwork phase: drop it so mark_complete fires as
            # soon as the other phases report done (else the bar stalls
            # at 60% waiting for an artwork emit that never comes).
            self._post_sync_pending.discard("artwork")

    def _arm_watchdog(self) -> None:
        """(Re)arm the post-sync completion watchdog, cancelling any prior.

        The try/finally guards in MetadataService and ArtworkService
        normally emit POST_SYNC_PHASE_CHANGED before this fires; the
        watchdog only matters if both safeguards are bypassed.
        """
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = asyncio.create_task(
            self._post_sync_watchdog(), name="post-sync-watchdog",
        )

    async def _emit_complete(
        self,
        result: SyncResult,
        libraries: dict[str, list[Game]],
        errors: dict[str, str],
        duration_ms: int,
        total_games: int,
        *,
        fetch_artwork: bool,
        resync_artwork: bool,
        is_force: bool,
    ) -> None:
        """Emit SYNC_COMPLETE (UI) + LIBRARY_SYNC_COMPLETED (activity log)."""
        await self._bus.emit(
            Events.SYNC_COMPLETE,
            games=result.games,
            stores_synced=list(libraries.keys()),
            # Every registered store, not just the ones that returned
            # games — lets ShortcutService.reconcile sweep stale
            # shortcuts for a logged-out / empty store (phantom Ubisoft
            # entries, the legacy microsoft:ms-auth row).
            registered_stores=self._registry.store_ids(),
            errors=errors,
            duration_ms=duration_ms,
            fetch_artwork=fetch_artwork,
            resync_artwork=resync_artwork,
            is_force=is_force,
        )
        await self._bus.emit(
            Events.LIBRARY_SYNC_COMPLETED,
            duration_ms=duration_ms,
            game_count=total_games,
            store_count=len(libraries),
            errors=dict(errors),
        )
        # Artwork skip is silent in ``_arm_artwork_phase``; the frontend
        # waits on it (seeded from ``registered_phases``), so signal it or
        # the set never drains and the restart modal never fires (UD-006).
        if not fetch_artwork and "artwork" in self._registered_phases:
            await self._bus.emit(
                Events.POST_SYNC_PHASE_CHANGED,
                phase="artwork", active=False, total=0, done=0,
            )

    async def _post_sync_watchdog(self) -> None:
        """Force-complete the sync if ``_post_sync_pending`` is still
        non-empty after ``POST_SYNC_WATCHDOG_SECONDS``.

        Should never fire in practice — try/finally in the post-sync
        services guarantees the phase-done events. Exists for the
        pathological case where ``bus.emit`` itself raises or a task is
        killed before reaching its finally block.
        """
        try:
            await asyncio.sleep(POST_SYNC_WATCHDOG_SECONDS)
        except asyncio.CancelledError:
            return
        if self._post_sync_pending:
            logger.warning(
                "[SyncService] post-sync watchdog tripped: phases %s "
                "never reported done after %ds — forcing completion",
                sorted(self._post_sync_pending),
                POST_SYNC_WATCHDOG_SECONDS,
            )
            self._post_sync_pending.clear()
            # Don't clobber a cancelled status — the user explicitly
            # requested cancel and the bar should reflect that.
            if self._progress.status != "cancelled":
                self._progress.mark_complete()
            self._bus.set_sync_progress(None)
