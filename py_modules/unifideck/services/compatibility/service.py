"""CompatibilityService — post-sync ProtonDB + Deck-Verified fetcher.

Subscribes to ``SYNC_COMPLETE`` and walks the game list, resolving
each title to its compat rating via :class:`CompatLibrary`. Mirrors
the pattern of :mod:`unifideck.services.metadata_service` (fire-and-
forget background task, ``POST_SYNC_PHASE_CHANGED`` on completion,
tick-per-game progress, cancel-checkpoint between iterations).

Why this is its own service
===========================
* The compat fetch is HTTP-heavy (~50ms per title on a good day,
  longer when ProtonDB is grumpy). Coupling it to MetadataService
  would mean a single failure window for two unrelated data sources.
* Compat ratings update independently of metadata (a tier change on
  ProtonDB doesn't invalidate the Steam Store payload), so a
  separate cache namespace + lifecycle is cleaner.
* The phase has its own progress band (95-98) on the UI, so the user
  sees what's happening — the staging behaviour every user is
  trained on.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from unifideck.compatibility import CompatLibrary
from unifideck.core.types import Game
from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.core.sync_service import SyncService
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Per-game concurrency cap for the compat fetch loop. Empirically
# tuned via tmp_test_compat_limits.py — ProtonDB + Steam's
# saleaction endpoint both tolerate 16+ concurrent calls without
# throttling. 10 gives ~7× speedup over the old sequential+50ms
# pacing (8 min → ~1 min on a 1130-game library) with comfortable
# headroom. Overridable via ``compat.max_concurrent`` config.
DEFAULT_MAX_CONCURRENT = 10


class CompatibilityService:
    """Resolves ProtonDB / Deck-Verified ratings after each sync."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        sync_service: SyncService | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Store collaborators + register the phase + auto-wire handlers.

        ``sync_service`` is optional so the service can be constructed
        in test contexts without the full bootstrap, but registering
        the ``proton_meta`` phase is what makes ``mark_complete``
        wait for our done-event — without it the progress bar races
        to 100% before we've ticked.
        """
        self._bus = bus
        self._cache = cache
        self._config = config
        # Deferred writes: the per-sync loop writes once per game;
        # ``_run_enrichment``'s ``finally`` flushes both namespaces.
        self._lib = CompatLibrary(
            cache=cache, config=config, deferred_writes=True,
        )
        self._enrichment_task: asyncio.Task[None] | None = None
        if sync_service is not None:
            sync_service.register_post_sync_phase("proton_meta")
        auto_wire(self, self._bus)

    async def stop(self) -> None:
        """Lifecycle hook — let any in-flight enrichment task finish."""
        if self._enrichment_task is not None and not self._enrichment_task.done():
            try:
                await asyncio.wait_for(self._enrichment_task, timeout=5.0)
            except (TimeoutError, Exception):
                self._enrichment_task.cancel()

    def wire_sync_service(self, sync_service: SyncService) -> None:
        """Post-construction injection of the SyncService reference.

        SyncService and CompatibilityService are built in separate
        bootstrap layers (4 and 5 respectively). The constructor
        accepts ``sync_service=None`` so it can be built without
        knowing about the future SyncService instance; this setter
        is called after Layer 5 finishes, registering the
        ``proton_meta`` phase so ``mark_complete`` waits for our
        done-event.
        """
        sync_service.register_post_sync_phase("proton_meta")

    @subscribe(Events.SYNC_CANCELLED)
    async def _on_sync_cancelled(self, **_kwargs: Any) -> None:
        """Cancel the in-flight ProtonDB lookup loop on user cancel."""
        task = self._enrichment_task
        if task is not None and not task.done():
            task.cancel()

    @subscribe(Events.POST_SYNC_PHASE_CHANGED)
    async def _on_artwork_phase_done(self, **kwargs: Any) -> None:
        """Schedule background compat enrichment after Artwork finishes.

        Previously subscribed directly to ``SYNC_COMPLETE`` and
        raced ArtworkService + MetadataService for Steam's
        ``storesearch`` endpoint. Switching to wait for Artwork's
        phase-done event serialises the chain
        Metadata → Artwork → Compat, so by the time we start the
        ``steam_real_appid`` cache is fully populated and every
        ProtonDB lookup can short-circuit the ``search_store`` call.

        Fires only on the precise ``phase="artwork", active=False``
        flank to avoid reacting to every phase emit on the bus.
        Falls back to ``kwargs`` directly if ``sync_kwargs`` isn't
        present (defensive — supports older emitters that haven't
        been migrated yet).
        """
        if kwargs.get("phase") != "artwork":
            return
        if kwargs.get("active") is not False:
            return
        sync_kwargs = kwargs.get("sync_kwargs") or {}
        games = sync_kwargs.get("games") or kwargs.get("games", [])
        is_force = bool(sync_kwargs.get("is_force") or kwargs.get("is_force"))
        prior = self._enrichment_task
        if prior is not None and not prior.done():
            prior.cancel()
        self._enrichment_task = asyncio.create_task(
            self._run_enrichment(games, is_force=is_force),
            name="compatibility-enrichment",
        )

    async def _run_enrichment(
        self, games: list[Game], *, is_force: bool = False,
    ) -> None:
        """Per-game ProtonDB + Deck-Verified lookup, concurrent under a semaphore.

        Standard sync partitions out games whose rating is already
        cached (mirrors ``MetadataService._partition_games``) — they
        tick the progress counter instantly and cost zero HTTP.
        ``is_force`` skips the partition and refreshes every entry.
        """
        total = len(games)
        progress = self._bus.get_sync_progress() if hasattr(self._bus, "get_sync_progress") else None
        try:
            if not games:
                return
            if progress is not None:
                progress.start_compat(total)
            skipped, pending = (
                ([], list(games)) if is_force
                else self._partition_games(games)
            )
            logger.info(
                "[CompatibilityService] compat fetch started for %d games "
                "(%d skipped (cached), %d pending, force=%s)",
                total, len(skipped), len(pending), is_force,
            )
            await self._tick_skipped(skipped, progress)
            if pending:
                await self._fetch_pending(
                    pending, progress, total, refresh=is_force,
                )
        finally:
            self._flush_compat_caches()
            await self._bus.emit(
                Events.POST_SYNC_PHASE_CHANGED,
                phase="proton_meta", active=False, total=total, done=total,
            )
            logger.info(
                "[CompatibilityService] compat fetch finished (%d games)",
                total,
            )

    @staticmethod
    async def _tick_skipped(skipped: list[Game], progress: Any | None) -> None:
        """Advance the progress counter instantly for cached games."""
        if progress is None:
            return
        for g in skipped:
            await progress.increment_compat(g.title)

    async def _fetch_pending(
        self,
        pending: list[Game],
        progress: Any | None,
        total: int,
        *,
        refresh: bool,
    ) -> None:
        """Fan out the per-game lookups under one shared session.

        One session per run (``ssl=False`` — the permissive-TLS
        invariant): per-call sessions cost two TLS handshakes per
        game on a cold sync.
        """
        sem = asyncio.Semaphore(self._max_concurrent())
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as sess:
            tasks = [
                asyncio.create_task(
                    self._fetch_one(
                        g, sem, progress, refresh=refresh, session=sess,
                    ),
                )
                for g in pending
            ]
            await self._drain(tasks, progress, total)

    def _flush_compat_caches(self) -> None:
        """Persist the loop's deferred compat/appid-mapping writes."""
        for namespace in ("compat", "steam_real_appid"):
            try:
                self._cache.flush(namespace)
            except Exception:
                logger.debug(
                    "[CompatibilityService] cache flush %s failed", namespace,
                )

    def _partition_games(
        self, games: list[Game],
    ) -> tuple[list[Game], list[Game]]:
        """Split games into ``(already-cached, pending-fetch)``.

        Mirrors ``MetadataService._partition_games``. Before this
        partition existed the compat phase visited every game every
        sync: titles that never resolve on Steam re-ran
        ``search_store`` forever, and entries with no published
        test results re-hit the Deck-Verified endpoint forever.
        """
        skipped: list[Game] = []
        pending: list[Game] = []
        for g in games:
            (skipped if self._has_cached_compat(g) else pending).append(g)
        return skipped, pending

    def _has_cached_compat(self, game: Game) -> bool:
        """True when this sync could fetch nothing new for ``game``."""
        mapping = self._lib.cached_steam_mapping(game.app_id)
        if mapping is None:
            # Never resolved — worth an attempt (backfills the
            # mapping the metadata phase missed).
            return False
        if mapping <= 0:
            # Negative-cached: no Steam counterpart exists. Only a
            # force sync (via the metadata phase's re-resolution)
            # retries these.
            return True
        entry = self._cached_compat_entry(mapping)
        if entry is None:
            return False
        return not self._needs_self_heal(entry)

    def _cached_compat_entry(self, steam_id: int) -> dict[str, Any] | None:
        """Read the ``compat`` cache entry for a real Steam AppID."""
        try:
            value = self._cache.get("compat", str(steam_id))
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _needs_self_heal(entry: dict[str, Any]) -> bool:
        """Pre-``deck_test_results`` cache entries get ONE upgrade fetch.

        The ``dtr_checked`` stamp (written by ``CompatLibrary``)
        marks the upgrade as attempted so games with genuinely no
        published test results stop re-fetching every sync.
        """
        return (
            entry.get("deck_status", "unknown") != "unknown"
            and not entry.get("deck_test_results")
            and not entry.get("dtr_checked")
        )

    async def _fetch_one(
        self,
        game: Game,
        sem: asyncio.Semaphore,
        progress: Any | None,
        *,
        refresh: bool = False,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Per-game lookup body — under the semaphore.

        ``increment_compat`` runs unconditionally so the UI counter
        ticks even when the upstream call raises (failure → "we
        attempted this game", not a stall).
        """
        async with sem:
            try:
                # Pass the shortcut AppID so CompatLibrary can reuse
                # the ``steam_real_appid`` cache populated by
                # MetadataService — skips a per-game storesearch.
                await self._lib.get_for_title(
                    game.title, shortcut_app_id=game.app_id,
                    refresh=refresh, session=session,
                )
            except Exception as e:
                logger.debug(
                    "[CompatibilityService] compat fetch failed for %s: %s",
                    game.title, e,
                )
            if progress is not None:
                await progress.increment_compat(game.title)

    async def _drain(
        self, tasks: list[asyncio.Task[None]], progress: Any | None, total: int,
    ) -> None:
        """Await tasks as they finish; honour the cancel-status flank."""
        for done_count, fut in enumerate(asyncio.as_completed(tasks)):
            if progress is not None and progress.status == "cancelled":
                logger.info(
                    "[CompatibilityService] cancel detected at %d/%d — aborting",
                    done_count, total,
                )
                for t in tasks:
                    if not t.done():
                        t.cancel()
                break
            try:
                await fut
            except Exception:
                logger.debug(
                    "[CompatibilityService] drained task raised", exc_info=True,
                )

    def _max_concurrent(self) -> int:
        """Read ``compat.max_concurrent`` from config or fall back to default."""
        if self._config is None:
            return DEFAULT_MAX_CONCURRENT
        try:
            value = self._config.get(
                "compat.max_concurrent", DEFAULT_MAX_CONCURRENT,
            )
        except Exception:
            return DEFAULT_MAX_CONCURRENT
        try:
            n = int(value)
        except (TypeError, ValueError):
            return DEFAULT_MAX_CONCURRENT
        return max(1, n)
