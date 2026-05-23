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
from unifideck.stores import StoreRegistry

from .sync_dedup_mixin import _SyncDedupMixin
from .sync_queries_mixin import _SyncQueriesMixin
from .types import Events, Game, SyncRequest, SyncResult

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.stores.shared.store_base import StoreBase

logger = logging.getLogger(__name__)

# Watchdog timeout for post-sync hooks. Real syncs of 1000+ games
# spend 15-30 minutes in the metadata-enrichment phase, so this is
# generous on purpose — it only catches pathological stuck states
# (bus.emit raises, task is killed without reaching its finally).
# Try/finally guards in MetadataService and ArtworkService are the
# primary completion path; this is the safety net.
POST_SYNC_WATCHDOG_SECONDS = 1800

# Cooldown defaults — 5 seconds matches staging. Users can override
# via ``sync.cooldown_seconds`` in config.
DEFAULT_COOLDOWN_SECONDS = 5
DEFAULT_COOLDOWN_MS = DEFAULT_COOLDOWN_SECONDS * 1000


class SyncService(_SyncQueriesMixin, _SyncDedupMixin):
    """Single-flight multi-store library sync orchestrator.

    Inherits read-only query methods (``get_status``,
    ``get_all_games``, ``get_games_by_store``, ``get_game_info``,
    ``_flatten``) from :class:`_SyncQueriesMixin`. The split is
    purely about file size — externally this class still
    exposes the same API surface it always did.
    """

    def __init__(
        self,
        registry: StoreRegistry,
        bus: EventBus,
        config: ConfigManager | None = None,
        launcher_path: str = "",
        cache: CacheManager | None = None,
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
            cache: optional ``CacheManager``. When provided,
                a snapshot is taken at sync start and
                restored on cancel — protects metadata /
                store-library caches from partial writes
                when the user aborts mid-sync.
        """
        self._registry = registry
        self._bus = bus
        self._config = config
        self._cache = cache
        # Cooldown read from config at construction time — the value
        # is small enough that a re-read at every sync would be
        # overkill; the user can restart the plugin to pick up changes.
        self._cooldown_ms = self._resolve_cooldown_ms()
        # Absolute path to ``bin/unifideck-launcher``. Combined
        # with the game title to produce a stable Steam-shortcut
        # AppID via ``generate_app_id``. The launcher path never
        # changes once the plugin is installed, so the AppID is
        # invariant across install / uninstall transitions — that
        # invariant is load-bearing for ShortcutService.reconcile
        # (which keys on app_id to detect orphans) and for
        # ArtworkService.fetch_artwork (which writes covers to
        # ``{grid_dir}/{app_id}p.jpg``).
        self._launcher_path = launcher_path
        self._lock = asyncio.Lock()
        # Smaller, faster lock guarding ``_pending_request`` reads
        # and writes. Distinct from ``_lock`` (which gates whole
        # sync runs) so an enqueue from another task doesn't have
        # to wait for the in-flight sync to finish.
        self._request_lock = asyncio.Lock()
        self._pending_request: SyncRequest | None = None
        self._cancel_event = asyncio.Event()
        self._all_games: dict[str, list[Game]] = {}
        self._last_sync_time: float | None = None
        self._current_store: str | None = None
        # Per-sync-run progress tracker, consumed by the frontend's
        # 500ms polling loop via ``get_sync_progress → to_dict()``.
        # Ported from staging's ``SyncProgress`` class (phase-range
        # allocation, per-phase sub-counters, i18n labels).
        from unifideck.core.sync_progress import SyncProgress
        self._progress = SyncProgress()
        self._post_sync_pending: set[str] = set()
        # Phase names that post-sync services have registered as
        # "I will emit POST_SYNC_PHASE_CHANGED for this phase". Used
        # to populate ``_post_sync_pending`` at the start of every
        # finalize so mark_complete only fires once every registered
        # service has reported done. Initialised with the always-on
        # phases ("artwork", "metadata"); other services
        # (Compatibility, etc.) call :meth:`register_post_sync_phase`
        # at bootstrap.
        self._registered_phases: set[str] = {"artwork", "metadata"}
        self._watchdog_task: asyncio.Task[None] | None = None
        # In-flight per-store fetch task, held so :meth:`cancel`
        # can interrupt a slow ``store.get_library()`` mid-await
        # without waiting for the await to return normally.
        self._current_store_task: (
            asyncio.Task[tuple[list[Game], str | None]] | None
        ) = None
        # Snapshot of every CacheManager store, captured at
        # ``_setup_sync`` time and consumed on cancel. ``None``
        # outside of an active sync.
        self._cache_snapshot: dict[str, dict[str, Any]] | None = None
        self._bus.on(
            Events.POST_SYNC_PHASE_CHANGED, self._on_post_sync_phase,
        )
        # Surface registry/appid drift in logs at boot — operators
        # see it without needing a separate RPC. The audit doesn't
        # mutate anything; fixing drift is a manual / admin action.
        self._audit_appid_drift_on_boot()

    def _audit_appid_drift_on_boot(self) -> None:
        """Log any shortcut-registry appid drift detected at startup.

        Defensive try/except — a registry-read failure here must
        not block plugin boot. The drift report is diagnostic;
        absent or unreadable registry just means "nothing to audit
        yet" (first run, fresh install).
        """
        if not self._launcher_path:
            return
        try:
            from unifideck.services.shortcut.migrations import (
                audit_appid_drift,
            )
            audit_appid_drift(self._launcher_path)
        except Exception:
            logger.debug(
                "[SyncService] appid-drift audit skipped (registry unreadable)",
                exc_info=True,
            )

    async def sync_all(
        self,
        *,
        force: bool = False,
        fetch_artwork: bool = True,
        resync_artwork: bool = False,
        source: str = "manual",
    ) -> SyncResult:
        """Run a full multi-store sync. Queues behind an in-flight sync.

        Wraps the args in a :class:`SyncRequest` and dispatches
        through :meth:`_enqueue`. A second concurrent call no longer
        bounces with ``error="sync_already_running"`` — instead, the
        request merges into ``_pending_request`` and runs as soon as
        the current sync releases the lock. The response carries
        ``restart_pending=True`` so the caller / frontend knows a
        second sync is on its way.

        ``force=True`` is reserved for tests and admin actions that
        need to bypass the queue entirely. Production callers should
        leave it at False.

        Args:
            force: bypass the lock + queue. Synchronous re-entry;
                use sparingly.
            fetch_artwork: when ``False``, skip the artwork phase
                (background syncs that only need a fresh game list).
            resync_artwork: when ``True``, ArtworkService clears
                its SGDB cache + ignores ``has_artwork`` so every
                game gets a fresh download.
            source: provenance string — ``"manual"`` (default),
                ``"auth:<store>"`` (from :meth:`request_auth_sync`),
                ``"background"``, ``"scheduled"``. Surfaces in logs
                and on the returned :class:`SyncResult`.

        Returns:
            ``SyncResult`` from the full sync, or a queued-response
            when the request was deferred.
        """
        request = SyncRequest(
            kind="force" if force else "sync",
            source=source,
            fetch_artwork=fetch_artwork,
            resync_artwork=resync_artwork,
        )
        is_force = request.kind == "force"
        if force:
            # Force path is a hard bypass — no queue interaction so
            # tests / admin actions can drive the loop without
            # interference. Skip _enqueue; go straight to the lock.
            async with self._lock:
                return await self._run_sync(
                    fetch_artwork=fetch_artwork,
                    resync_artwork=resync_artwork,
                    is_force=is_force,
                )
        return await self._enqueue(request)

    async def _enqueue(self, request: SyncRequest) -> SyncResult:
        """Queue or run a :class:`SyncRequest`. Merges if a sync is in flight.

        Two paths:

        * **Lock free** — acquire it, drain the queue (merging in
          any later requests that arrived while we were waiting),
          run ``_run_sync``. After completion, if a new request was
          enqueued during the run, recurse to run it too.
        * **Lock held** — merge into ``_pending_request`` (force
          wins, flags OR together) and return a "queued"
          :class:`SyncResult` with ``restart_pending=True``.

        The merge step is what makes auth-chained syncs work — login
        finishes mid-sync, post-auth request arrives, gets folded
        into the queue, runs automatically once the current sync
        completes.
        """
        if self._lock.locked():
            async with self._request_lock:
                merged = (
                    self._pending_request.merge(request)
                    if self._pending_request is not None
                    else request
                )
                self._pending_request = merged
            logger.info(
                "[SyncService] sync request queued behind in-flight "
                "(source=%s, kind=%s)",
                request.source, request.kind,
            )
            return SyncResult(
                success=True,
                games=[],
                count=0,
                duration_ms=0,
                restart_pending=True,
                source=request.source,
            )
        async with self._lock:
            current = request
            while True:
                result = await self._run_sync(
                    fetch_artwork=current.fetch_artwork,
                    resync_artwork=current.resync_artwork,
                    is_force=current.kind == "force",
                )
                result.source = current.source
                # Drain anything queued during the run.
                async with self._request_lock:
                    next_req = self._pending_request
                    self._pending_request = None
                if next_req is None:
                    return result
                logger.info(
                    "[SyncService] draining queued sync (source=%s, kind=%s)",
                    next_req.source, next_req.kind,
                )
                current = next_req

    def _resolve_cooldown_ms(self) -> int:
        """Read ``sync.cooldown_seconds`` from config, default 5s.

        Called once at init — the value is small enough that re-reading
        at every sync is overhead without benefit. Users who change the
        config must restart the plugin to pick up the new value.
        """
        if self._config is None:
            return DEFAULT_COOLDOWN_MS
        try:
            seconds = self._config.get("sync.cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
            ms = int(float(seconds) * 1000)
        except (TypeError, ValueError):
            return DEFAULT_COOLDOWN_MS
        return max(ms, 0)

    def register_post_sync_phase(self, phase: str) -> None:
        """Declare that a post-sync service will emit ``phase``-done events.

        Called by services at bootstrap (e.g. ``CompatibilityService``
        calling ``register_post_sync_phase("proton_meta")``). The
        registered phase gets added to ``_post_sync_pending`` at the
        start of every sync, so ``mark_complete`` only fires once
        every registered service has reported done.

        Without this, a service whose ``POST_SYNC_PHASE_CHANGED``
        emit hadn't been pre-declared would be ignored — and the
        progress bar would race ahead to "complete" before the
        service finished its work.
        """
        self._registered_phases.add(phase)

    async def request_auth_sync(self, store: str) -> SyncResult:
        """Queue a post-login sync. Called by AuthDispatcher after store auth.

        Without this, a successful store login while a sync is
        running would silently drop the refresh — the user just
        logged in and would have to manually press Sync to see
        their newly-available titles. Routing through ``_enqueue``
        means the new library shows up the moment the current sync
        finishes.
        """
        return await self.sync_all(source=f"auth:{store}")

    async def _run_sync(
        self,
        *,
        fetch_artwork: bool = True,
        resync_artwork: bool = False,
        is_force: bool = False,
    ) -> SyncResult:
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

        Refactor history (2026-05-14): pulled
        ``_sync_no_stores_shortcircuit`` and
        ``_sync_cancelled_result`` to bring this function
        under the 80-line cap. Then ``_setup_sync`` and
        ``_finalize_sync`` were extracted to bring fan-out
        under the 10-callee cap — this function is now a
        flat read of the orchestration skeleton, with each
        phase delegated to a focused helper.
        """
        started, available_stores = await self._setup_sync()
        total = len(available_stores)
        if total == 0:
            return await self._sync_no_stores_shortcircuit()
        libraries: dict[str, list[Game]] = {}
        errors: dict[str, str] = {}
        for idx, store in enumerate(available_stores):
            if self._cancel_event.is_set():
                return await self._sync_cancelled_result(
                    idx, total, libraries,
                )
            self._current_store = store.store_name
            await self._emit_progress(store.store_name, idx, total)
            # Run the store fetch as a tracked task so :meth:`cancel`
            # can interrupt it mid-await. Without this, ``cancel()``
            # only sets ``_cancel_event`` and the loop doesn't notice
            # until the current ``await store.get_library()`` returns
            # — which can take 30+ seconds on slow stores. Task
            # cancellation propagates CancelledError through the
            # store's HTTP/subprocess awaits, ending the fetch
            # within milliseconds.
            self._current_store_task = asyncio.create_task(
                self._sync_one_store(store),
                name=f"sync-store-{store.store_name}",
            )
            try:
                games, err = await self._current_store_task
            except asyncio.CancelledError:
                games, err = [], "cancelled"
            finally:
                self._current_store_task = None
            libraries[store.store_name] = games
            if err is not None:
                errors[store.store_name] = err
            if self._cancel_event.is_set():
                return await self._sync_cancelled_result(
                    idx + 1, total, libraries,
                )
        self._current_store = None
        try:
            return await self._finalize_sync(
                libraries, errors, total, started,
                fetch_artwork=fetch_artwork,
                resync_artwork=resync_artwork,
                is_force=is_force,
            )
        except Exception:
            # ``_finalize_sync`` does cache-destructive work (clears
            # ``sgdb_fetch`` when ``resync_artwork=True``, populates
            # app_ids, etc.). If anything raises after a partial
            # clear, the snapshot captured in ``_setup_sync``
            # restores the pre-sync state so the next sync starts
            # from known-good caches.
            logger.exception("[SyncService] _finalize_sync raised — restoring caches")
            self._restore_cache_snapshot()
            raise

    async def _setup_sync(self) -> tuple[float, list[StoreBase]]:
        """Reset cancel flag, snapshot the registry, emit SYNC_STARTED.

        Pulled out of ``_run_sync`` so the orchestration loop
        doesn't carry the setup phase's call targets. Pairs
        with ``_finalize_sync`` to keep the fan-out under cap.

        Returns:
            ``(started, available_stores)`` — monotonic start
            marker (consumed by ``_finalize_sync``) and the
            store snapshot used as the progress denominator.
        """
        self._cancel_event.clear()
        # Capture every cache's state so a cancel mid-sync can roll
        # back to consistent pre-sync state. Without this, a sync
        # that's cancelled after the metadata phase wrote a few
        # entries — but before all of them — leaves the cache half
        # populated; the next sync skips the "missing" entries on
        # the cooldown rule and they never get filled in.
        if self._cache is not None:
            self._cache_snapshot = self._cache.snapshot()
        else:
            self._cache_snapshot = None
        started = time.monotonic()
        available_stores = self._registry.available()
        store_names = [s.store_name for s in available_stores]
        self._progress.start_fetching(len(available_stores))
        self._bus.set_sync_progress(self._progress)
        await self._bus.emit(
            Events.SYNC_STARTED,
            stores=store_names,
            scope="all",
        )
        # Durable activity event — ephemeral SYNC_STARTED above
        # drives UI; this one feeds the persistent log via
        # ActivityLogService so the user can see "last 10 syncs".
        await self._bus.emit(
            Events.LIBRARY_SYNC_STARTED,
            stores=store_names,
            started_at_ms=int(time.time() * 1000),
        )
        logger.info(
            "[SyncService] sync starting (%d stores)", len(available_stores),
        )
        return started, available_stores

    def _restore_cache_snapshot(self) -> None:
        """Roll caches back to the pre-sync snapshot if one was taken.

        Idempotent — clears the snapshot after restoring so a second
        call (e.g. cancel happens twice in rapid succession) is a
        no-op. Both branches log; silent failure here would mask the
        cache-state divergence the user is about to see.
        """
        if self._cache_snapshot is None or self._cache is None:
            return
        try:
            self._cache.restore(self._cache_snapshot)
            logger.info("[SyncService] cache snapshot restored after cancel")
        except Exception:
            logger.exception(
                "[SyncService] cache snapshot restore failed",
            )
        self._cache_snapshot = None

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
            is_force: whether this was a force-sync (kind="force"
                in ``SyncRequest``). Forwarded to ShortcutService
                via the SYNC_COMPLETE payload so it knows to UPDATE
                existing shortcuts rather than just KEEP them.

        Pulled out of ``_run_sync`` so the orchestration
        function doesn't carry the post-loop call targets
        (``monotonic`` again, ``_apply_dedup_and_emit``,
        ``time``, ``_aggregate_results``, ``emit``-for-complete).
        Pairs with ``_setup_sync``.

        Args:
            fetch_artwork: when ``False``, mark artwork phase
                as already-done in ``_post_sync_pending`` so
                ArtworkService can early-emit and the bar
                doesn't stall at 60%.
            resync_artwork: forwarded to ArtworkService via
                the SYNC_COMPLETE payload; the service treats
                it as ``force`` and bypasses the on-disk
                ``has_artwork`` skip check. Negative-cache
                cleared here so previously-failed games are
                retried.

        Side effects: updates ``self._all_games`` and
        ``self._last_sync_time``.
        """
        duration_ms = int((time.monotonic() - started) * 1000)
        libraries = await self._apply_dedup_and_emit(libraries)
        self._populate_app_ids(libraries)
        self._all_games = libraries
        total_games = sum(len(g) for g in libraries.values())
        self._progress.set_library_totals(total_games)
        # ``resync_artwork`` and ``fetch_artwork`` are forwarded
        # to ArtworkService via the SYNC_COMPLETE payload.
        # ArtworkService owns the SGDB failure-cooldown cache and
        # clears it when ``resync_artwork=True``; SyncService
        # doesn't hold a cache reference so it can't do that
        # directly without widening the constructor surface.
        # Signal the artwork phase BEFORE emitting SYNC_COMPLETE so
        # the frontend's polling loop sees the phase transition when
        # the bus event fires.
        if fetch_artwork:
            self._progress.start_artwork(total_games)
            self._post_sync_pending = set(self._registered_phases)
        else:
            # Skip the artwork phase entirely. Drop it from the
            # pending set so mark_complete fires as soon as the
            # other phases report done — without this the progress
            # bar would stall at 60% waiting for an artwork emit
            # that never comes (ArtworkService will see
            # fetch_artwork=False and early-emit, but
            # belt-and-suspenders).
            self._post_sync_pending = set(self._registered_phases)
            self._post_sync_pending.discard("artwork")
        self._last_sync_time = time.time()
        # Arm the post-sync watchdog (cancel any prior). The
        # try/finally guards in MetadataService and ArtworkService
        # will normally emit POST_SYNC_PHASE_CHANGED before this
        # fires; the watchdog only matters if both safeguards are
        # somehow bypassed.
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = asyncio.create_task(
            self._post_sync_watchdog(), name="post-sync-watchdog",
        )
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
            fetch_artwork=fetch_artwork,
            resync_artwork=resync_artwork,
            is_force=is_force,
        )
        # Durable activity record — ephemeral SYNC_COMPLETE above
        # drives the UI; this one persists to the activity log.
        await self._bus.emit(
            Events.LIBRARY_SYNC_COMPLETED,
            duration_ms=duration_ms,
            game_count=total_games,
            store_count=len(libraries),
            errors=dict(errors),
        )
        # Successful finalize — the snapshot was insurance against
        # mid-store cancel and we no longer need it. Releasing the
        # reference lets the GC reclaim it before the post-sync
        # phases start filling caches with fresh data.
        self._cache_snapshot = None
        return result

    async def _post_sync_watchdog(self) -> None:
        """Safety net: force-complete the sync if ``_post_sync_pending``
        is still non-empty after ``POST_SYNC_WATCHDOG_SECONDS``.

        Should never fire in practice — try/finally in the post-sync
        services guarantees the phase-done events. Exists for the
        pathological case where ``bus.emit`` itself raises or a task
        is killed before reaching its finally block. Without this,
        the progress bar would stay stuck below 100% forever and
        the frontend would never see ``status="complete"``.
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

    def _populate_app_ids(
        self, libraries: dict[str, list[Game]],
    ) -> None:
        """Assign every ``Game`` a stable Steam-shortcut AppID.

        Per-store sync methods construct ``Game`` records with
        ``app_id=0`` (the dataclass default) because the AppID
        depends on plugin-install state they don't know about.
        We fill it in here, once, so every downstream consumer
        (ShortcutService.reconcile, ArtworkService.fetch_artwork,
        MetadataService.fetch_appdetails_for_game, the frontend
        SteamStorePatcher) sees a populated id.

        The AppID is ``crc32(launcher_path + title) | 0x80000000``
        — see :func:`generate_app_id`. Anchoring on the launcher
        path (not the per-game ``exe_path``) keeps the id stable
        when the user installs or uninstalls the game.
        """
        if not self._launcher_path:
            logger.warning(
                "[SyncService] launcher_path unset — game.app_id "
                "will not be populated, shortcuts cannot be created",
            )
            return
        from unifideck.services.shortcut.games_map import generate_app_id

        filled = 0
        for games in libraries.values():
            for game in games:
                if game.app_id:
                    continue
                game.app_id = generate_app_id(
                    self._launcher_path, game.title,
                )
                filled += 1
        if filled:
            logger.info(
                "[SyncService] populated app_id for %d games", filled,
            )

    async def _sync_no_stores_shortcircuit(self) -> SyncResult:
        """Emit SYNC_COMPLETE with an empty payload and return.

        Used when the registry exposes zero available stores —
        a legitimate state (e.g. all stores in offline mode),
        not an error. The empty SYNC_COMPLETE keeps any UI
        listener in sync with backend reality.
        """
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

    async def _sync_cancelled_result(
        self,
        idx: int,
        total: int,
        libraries: dict[str, list[Game]],
    ) -> SyncResult:
        """Emit SYNC_CANCELLED and return the partial result.

        The result carries any games already fetched so the
        caller can decide what to do with them (typically:
        keep showing the previously-synced state). Also rolls
        every cache back to the pre-sync snapshot so partial
        writes from the per-store loop don't persist.
        """
        logger.info(
            "[SyncService] sync cancelled at store %d/%d",
            idx,
            total,
        )
        self._restore_cache_snapshot()
        self._progress.mark_cancelled()
        await self._bus.emit(Events.SYNC_CANCELLED)
        await self._bus.emit(
            Events.LIBRARY_SYNC_CANCELLED,
            store_count=total,
            cancelled_at_store=idx,
        )
        return SyncResult(
            success=False,
            error="cancelled",
            games=self._flatten(libraries),
        )


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
            self._all_games = {}  # type: ignore[unreachable]  # fallback for store registry miss
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

    def _on_post_sync_phase(self, **kwargs: Any) -> None:
        """Handle POST_SYNC_PHASE_CHANGED (completion only).

        Phase starts are triggered by SyncService directly.
        Completions are tracked in ``_post_sync_pending`` —
        only when BOTH artwork and metadata report done do we
        mark_complete(). This prevents a race where metadata
        finishes before artwork (both tasks are spawned
        concurrently at SYNC_COMPLETE time).

        Note: ``start_metadata`` is owned by ``MetadataService._run_enrichment``
        (it calls ``progress.start_metadata(len(games))`` itself).
        This handler no longer cross-wires the metadata total from
        the artwork count — that miswired the denominator and made
        the metadata phase overshoot 100%.
        """
        phase = kwargs.get("phase")
        active = bool(kwargs.get("active", True))
        if active:
            return
        total = kwargs.get("total", 0)
        if phase == "artwork":
            self._progress.artwork_synced = total
        elif phase == "metadata":
            self._progress.metadata_synced = total
        pending = getattr(self, "_post_sync_pending", set())
        pending.discard(phase)
        if not pending:
            # Preserve a cancelled status — services' try/finally
            # still emits the phase-done event after cancel, which
            # would otherwise flip status from "cancelled" to
            # "complete" and hide the cancellation from the user.
            if self._progress.status != "cancelled":
                self._progress.mark_complete()
            self._bus.set_sync_progress(None)

    async def _emit_progress(self, store_name: str, idx: int, total: int) -> None:
        """Emit ``SYNC_PROGRESS`` — updates the phase tracker + fires event."""
        total_games = sum(len(g) for g in self._all_games.values())
        self._progress.start_store_sync(store_name, idx, total)
        await self._bus.emit(
            Events.SYNC_PROGRESS,
            store=store_name,
            progress_percent=self._progress.progress_percent,
            total_games=total_games,
            synced_games=total_games,
            current_game=self._progress.current_game,
            status=self._progress.status,
        )

    async def cancel(self) -> bool:
        """Request cancellation of the in-flight sync.

        Cooperative: the sync loop checks ``self._cancel_event``
        between stores; ArtworkService and MetadataService check
        ``progress.status == "cancelled"`` between their per-game
        iterations. The bus emit signals services that don't poll
        the progress object (so they can flush queued work).

        Returns ``False`` immediately if no sync is running, but
        ``True`` covers both per-store-loop cancel and
        post-sync-phase cancel — the running code finds out via
        ``_cancel_event`` and/or ``progress.status`` and exits at
        its next checkpoint.
        """
        if not self._lock.locked():
            return False
        self._cancel_event.set()
        # Mark the progress as cancelled so MetadataService /
        # ArtworkService loops checking ``progress.status`` see
        # the change at their next iteration (essential for
        # cancellation mid-post-sync, where the per-store loop
        # has already returned).
        self._progress.mark_cancelled()
        # Forcefully interrupt the in-flight store fetch so the
        # loop doesn't have to wait for the current
        # ``store.get_library()`` to finish. CancelledError
        # propagates through the store's HTTP/subprocess awaits
        # and the wrapping ``except asyncio.CancelledError`` in
        # ``_run_sync`` turns it into a clean cancelled-result.
        current_task = self._current_store_task
        if current_task is not None and not current_task.done():
            current_task.cancel()
        # Broadcast so listeners that don't poll the progress
        # tracker still get notified (frontend SyncContext
        # already listens for SYNC_CANCELLED). Idempotent — if
        # the per-store loop emits this too, the second emit
        # has no observable effect.
        await self._bus.emit(Events.SYNC_CANCELLED)
        logger.info("[SyncService] cancel requested")
        return True

    # Read-only query API (get_status / get_all_games /
    # get_games_by_store / get_game_info / _flatten) lives in
    # ``_SyncQueriesMixin`` — see ``sync_queries_mixin.py``.
