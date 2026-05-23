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

# Delay between per-game lookups so we don't hammer ProtonDB /
# Steam-Verified back to back. Matches staging's pacing.
DEFAULT_BULK_DELAY_MS = 50


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

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """Schedule background compat enrichment.

        Fire-and-forget so the SYNC_COMPLETE emit returns immediately.
        The task's try/finally guarantees the phase-done event fires
        whether the loop finishes, errors out, or is cancelled —
        otherwise ``_post_sync_pending`` strands ``proton_meta`` and
        the bar never reaches 100%.
        """
        games = kwargs.get("games", [])
        prior = self._enrichment_task
        if prior is not None and not prior.done():
            prior.cancel()
        self._enrichment_task = asyncio.create_task(
            self._run_enrichment(games),
            name="compatibility-enrichment",
        )

    async def _run_enrichment(self, games: list[Game]) -> None:
        """Per-game ProtonDB + Deck-Verified lookup loop."""
        total = len(games)
        delay_ms = self._delay_ms()
        progress = self._bus.get_sync_progress() if hasattr(self._bus, "get_sync_progress") else None
        try:
            if not games:
                return
            if progress is not None:
                progress.status = "proton_meta"
                progress.current_game = {
                    "label": "sync.fetchingEnhancedMetadata",
                    "values": {"synced": 0, "total": total},
                }
            logger.info(
                "[CompatibilityService] compat fetch started for %d games",
                total,
            )
            for done, game in enumerate(games, start=1):
                if progress is not None and progress.status == "cancelled":
                    logger.info(
                        "[CompatibilityService] cancel detected at %d/%d — aborting",
                        done, total,
                    )
                    break
                try:
                    await self._lib.get_for_title(game.title)
                except Exception as e:
                    logger.debug(
                        "[CompatibilityService] compat fetch failed for %s: %s",
                        game.title, e,
                    )
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)
        finally:
            await self._bus.emit(
                Events.POST_SYNC_PHASE_CHANGED,
                phase="proton_meta", active=False, total=total, done=total,
            )
            logger.info(
                "[CompatibilityService] compat fetch finished (%d games)",
                total,
            )

    def _delay_ms(self) -> int:
        """Read ``compat.bulk_delay_ms`` from config or fall back to 50."""
        if self._config is None:
            return DEFAULT_BULK_DELAY_MS
        try:
            value = self._config.get("compat.bulk_delay_ms", DEFAULT_BULK_DELAY_MS)
        except Exception:
            return DEFAULT_BULK_DELAY_MS
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULT_BULK_DELAY_MS
