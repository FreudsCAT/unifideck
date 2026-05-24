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
        self._lib = CompatLibrary(cache=cache, config=config)
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
        prior = self._enrichment_task
        if prior is not None and not prior.done():
            prior.cancel()
        self._enrichment_task = asyncio.create_task(
            self._run_enrichment(games),
            name="compatibility-enrichment",
        )

    async def _run_enrichment(self, games: list[Game]) -> None:
        """Per-game ProtonDB + Deck-Verified lookup, concurrent under a semaphore.

        Was sequential with a 50ms inter-game sleep — ~8 minutes
        for a 1130-game library. The semaphore cap is sized for
        the empirical ceiling of the slower of the two upstream
        endpoints; both tolerate 16+ in flight comfortably.
        """
        total = len(games)
        max_concurrent = self._max_concurrent()
        progress = self._bus.get_sync_progress() if hasattr(self._bus, "get_sync_progress") else None
        try:
            if not games:
                return
            if progress is not None:
                progress.start_compat(total)
            logger.info(
                "[CompatibilityService] compat fetch started "
                "for %d games (concurrency=%d)",
                total, max_concurrent,
            )
            sem = asyncio.Semaphore(max_concurrent)
            tasks = [
                asyncio.create_task(self._fetch_one(g, sem, progress))
                for g in games
            ]
            await self._drain(tasks, progress, total)
        finally:
            await self._bus.emit(
                Events.POST_SYNC_PHASE_CHANGED,
                phase="proton_meta", active=False, total=total, done=total,
            )
            logger.info(
                "[CompatibilityService] compat fetch finished (%d games)",
                total,
            )

    async def _fetch_one(
        self, game: Game, sem: asyncio.Semaphore, progress: Any | None,
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
        done_count = 0
        for fut in asyncio.as_completed(tasks):
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
            except Exception:  # noqa: BLE001 — per-game failures are best-effort
                pass
            done_count += 1

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
