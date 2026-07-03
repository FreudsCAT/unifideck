"""services/metadata_service.py — Game metadata resolver.

EventBus subscriber enriching ``Game`` objects with metadata
from 3 sources in priority order:
1. Steam Store — matches non-Steam games to their Steam app_id
   when one exists (real description, images, genres).
2. UnifiDB — Unifideck's own game database (niche + non-Steam).
3. Metacritic — scores and review summaries.

All responses cached (CacheManager) with a 7-day TTL to avoid
hammering third-party APIs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, cast

import aiohttp

from unifideck.core.types import Game
from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe
from unifideck.services import metadata_backfill, metadata_sources, pcgw_backfill

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

CACHE_NAMESPACE = "metadata"
# Two caches for the Steam Store patcher (SteamStorePatcher.ts).
# ``STEAM_REAL_APPID_NS`` maps each Unifideck shortcut's synthetic
# AppID to the real Steam Store AppID found by ``search_store``.
# ``STEAM_METADATA_NS`` holds the rich ``appdetails`` payload per
# real Steam AppID. The frontend reads both via dedicated RPCs.
STEAM_REAL_APPID_NS = "steam_real_appid"
STEAM_METADATA_NS = "steam_metadata"
STEAM_REVIEWS_NS = "steam_reviews"
SHORTCUT_ADDED_NS = "shortcut_added"
DEFAULT_CACHE_TTL = 7 * 24 * 3600  # fallback if config missing

# Per-game concurrency cap. Sized for Steam's ``appdetails`` rate
# limit (the binding constraint); UnifiDB / Metacritic are
# unconstrained on our side.
ENRICHMENT_CONCURRENCY = 5


def _cancel_pending(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel every task in ``tasks`` that hasn't finished yet."""
    for t in tasks:
        if not t.done():
            t.cancel()


class MetadataService:
    """Enriches Game objects with cross-store metadata."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        config: ConfigManager | None = None,
    ) -> None:
        """Store refs, read config, auto_wire."""
        self._bus = bus
        self._cache = cache
        self._config = config
        # Background enrichment task. Held so that a new
        # SYNC_COMPLETE can cancel the prior run before
        # starting its own — otherwise overlapping tasks both
        # increment ``SyncProgress.*_synced`` against the same
        # tracker, producing inflated numerators (the "1089/563"
        # symptom).
        self._enrichment_task: asyncio.Task[None] | None = None

        # NOTE: the cache TTL is owned by the registry, not the
        # service — see the ``"metadata"`` entry in
        # ``bootstrap/cache_registry.py``. ``metadata.cache_ttl``
        # in user config is currently unused; if per-user TTL
        # tuning is wanted, the right place to read it is at
        # ``register_default_caches`` time, not here.

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` guarded by
        # ``hasattr`` — but ``auto_wire`` is module-level,
        # not a bus method, so the hasattr check returned
        # False and every subscription was silently dropped.
        auto_wire(self, self._bus)

    async def stop(self) -> None:
        """Lifecycle hook — currently a no-op."""

    @subscribe(Events.SYNC_CANCELLED)
    async def _on_sync_cancelled(self, **_kwargs: Any) -> None:
        """Cancel any in-flight metadata enrichment immediately.

        User-initiated cancel must stop the per-game enrichment
        loop, not just the per-store fetch — otherwise the bar
        disappears but the 5-15 minutes of HTTP work keeps
        running in the background, ticking ``SyncProgress``
        counters that the user thought were dead.
        """
        task = self._enrichment_task
        if task is not None and not task.done():
            task.cancel()

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """Schedule enrichment as a background task and return immediately.

        Critical: the enrichment loop hits 3 HTTP APIs per game and
        sequentially-paces the Steam ``appdetails`` fetch (~0.25s
        per non-Steam game) — for 500+ games that's 5-15 minutes
        of work. Awaiting it inside this handler would block
        :meth:`asyncio.gather` in ``bus.emit(SYNC_COMPLETE, ...)``,
        which in turn blocks :meth:`SyncService._finalize_sync`,
        which holds ``SyncService._lock`` the entire time. The
        net effect on the user: "sync_all called while another
        sync is running — rejected" for the next 10+ minutes, and
        the frontend's ``await startMut.mutate()`` never resolves
        so the cooldown timer never starts.

        Solution: spawn the loop as a fire-and-forget task. The
        ``SYNC_COMPLETE`` emit returns immediately, the sync lock
        releases, the frontend gets its RPC response, and the
        enrichment quietly progresses in the background.
        """
        games = kwargs.get("games", [])
        # Cancel any prior enrichment still running. Two syncs
        # back-to-back (or a sync that was cancelled mid-enrich)
        # would otherwise leave the old task ticking
        # ``SyncProgress.*_synced`` on the same tracker the new
        # run just reset to 0 — the user sees a numerator larger
        # than the library size (e.g. "1089 / 563" with 563
        # games actually synced). Cancel is fire-and-forget: we
        # must NOT await it here (this handler runs inside
        # ``bus.emit(SYNC_COMPLETE)`` which is awaited by
        # ``SyncService._finalize_sync`` while holding the sync
        # lock; awaiting would re-introduce the multi-minute
        # lock-up that the fire-and-forget pattern fixed).
        prior = self._enrichment_task
        if prior is not None and not prior.done():
            prior.cancel()
        # Schedule unconditionally — even with games=[] the task
        # must run so its ``finally`` clause fires the phase-done
        # event. SyncService gates ``mark_complete`` on receiving
        # one POST_SYNC_PHASE_CHANGED per pending phase; a missing
        # signal strands ``_post_sync_pending`` and the progress
        # bar never reaches 100%.
        #
        # Stash the SYNC_COMPLETE kwargs so the phase-done emit can
        # forward them to downstream services (ArtworkService /
        # CompatibilityService) which now wait on this phase
        # instead of subscribing to SYNC_COMPLETE directly. This
        # serialises three previously-parallel pipelines that
        # were colliding on Steam's storesearch rate-limit.
        self._sync_kwargs = dict(kwargs)
        self._enrichment_task = asyncio.create_task(
            self._run_enrichment(games),
            name="metadata-enrichment",
        )

    def _has_complete_metadata(self, game: Game) -> bool:
        """Check if metadata is already fully cached for a game."""
        cache_key = f"{game.store}:{game.store_game_id}"
        # 1. Check general metadata cache (positive or negative)
        try:
            cached_meta = self._cache.get(CACHE_NAMESPACE, cache_key)
            if cached_meta is None:
                return False
        except Exception:
            return False

        # If it's a Steam-native game, general metadata is all we fetch
        if game.store == "steam":
            return True

        # 2. For non-Steam games, check if real Steam AppID resolution is cached
        try:
            steam_id = self._cache.get(STEAM_REAL_APPID_NS, str(game.app_id))
            if steam_id is None:
                return False
            if steam_id <= 0:
                # Resolved to negative (no Steam counterpart exists)
                return True

            # 3. If it maps to a real Steam AppID, check if appdetails are cached
            cached_details = self._cache.get(STEAM_METADATA_NS, str(steam_id))
            if cached_details is None:
                return False
        except Exception:
            return False

        return True

    async def _run_enrichment(self, games: list[Game]) -> None:
        """Background enrichment loop. ``finally`` emits
        ``POST_SYNC_PHASE_CHANGED(active=False)`` so the sync's
        post-phase tracker advances on success, exception, or
        user-initiated sync cancellation.
        """
        total = len(games)
        cancelled_by_replace = False
        try:
            if not games:
                return
            progress = self._sync_progress()
            if progress is not None:
                progress.start_metadata(total)
            logger.info(
                "[MetadataService] background enrichment started for %d games",
                total,
            )
            complete_games, pending_games = self._partition_games(games)
            await self._mark_complete_cached(complete_games, progress, total)
            if pending_games:
                await self._enrich_pending(
                    pending_games, progress, total, len(complete_games),
                )
        except asyncio.CancelledError:
            cancelled_by_replace = True
            logger.info(
                "[MetadataService] enrichment cancelled — newer sync took over",
            )
            raise
        finally:
            await self._finalize_enrichment(cancelled_by_replace, total, games)

    def _partition_games(
        self, games: list[Game],
    ) -> tuple[list[Game], list[Game]]:
        """Split games into ``(already-complete, pending-enrichment)``."""
        complete_games: list[Game] = []
        pending_games: list[Game] = []
        for g in games:
            if self._has_complete_metadata(g):
                complete_games.append(g)
            else:
                pending_games.append(g)
        return complete_games, pending_games

    async def _mark_complete_cached(
        self, complete_games: list[Game], progress: Any, total: int,
    ) -> None:
        """Instantly advance progress for games already fully cached."""
        if not complete_games:
            return
        logger.info(
            "[MetadataService] %d/%d games already have complete metadata cached",
            len(complete_games), total,
        )
        if progress is None:
            return
        for g in complete_games:
            await progress.increment_steam(g.title)
            await progress.increment_unifidb(g.title)

    async def _enrich_pending(
        self,
        pending_games: list[Game],
        progress: Any,
        total: int,
        complete_count: int,
    ) -> None:
        """Run concurrent enrichment for the games that are missing data."""
        logger.info(
            "[MetadataService] Enqueueing %d games missing metadata for enrichment",
            len(pending_games),
        )
        sem = asyncio.Semaphore(ENRICHMENT_CONCURRENCY)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                asyncio.create_task(
                    self._enrich_one_game(g, sem, session=session),
                )
                for g in pending_games
            ]
            await self._drain_enrichment(
                tasks, progress, total, start_count=complete_count,
            )

    async def _finalize_enrichment(
        self, cancelled_by_replace: bool, total: int, games: list[Game],
    ) -> None:
        """``finally`` body: emit the phase-done event and spawn long-tail
        backfills (skipped when a newer sync cancelled this run)."""
        if not cancelled_by_replace:
            # Forward SYNC_COMPLETE kwargs so the serialised
            # Artwork → Compat downstream chain reads them here.
            sync_kwargs = getattr(self, "_sync_kwargs", None) or {}
            await self._bus.emit(
                Events.POST_SYNC_PHASE_CHANGED,
                phase="metadata", active=False,
                total=total, done=total,
                sync_kwargs=sync_kwargs,
            )
        logger.info(
            "[MetadataService] background enrichment finished (%d games)",
            total,
        )
        # Long-tail Metacritic + PCGamingWiki lookups: fire-and-forget.
        if not cancelled_by_replace:
            metadata_backfill.spawn(self, games)
            pcgw_backfill.spawn(self, games)

    def _sync_progress(self) -> Any:
        """Return the bus's ``SyncProgress`` tracker, or ``None``."""
        if not hasattr(self._bus, "get_sync_progress"):
            return None
        return self._bus.get_sync_progress()

    async def _drain_enrichment(
        self,
        tasks: list[asyncio.Task[None]],
        progress: Any,
        total: int,
        start_count: int = 0,
    ) -> None:
        """Await every per-game task as it finishes, logging progress."""
        every = max(1, min(50, total // 5))
        done_count = start_count
        for fut in asyncio.as_completed(tasks):
            if progress is not None and progress.status == "cancelled":
                logger.info(
                    "[MetadataService] cancel detected at %d/%d — aborting",
                    done_count, total,
                )
                _cancel_pending(tasks)
                break
            try:
                await fut
            except Exception:
                logger.debug(
                    "[MetadataService] enrichment task raised", exc_info=True,
                )
            done_count += 1
            if done_count % every == 0:
                logger.info(
                    "[MetadataService] progress: %d/%d enriched",
                    done_count, total,
                )

    async def _enrich_one_game(
        self,
        game: Game,
        sem: asyncio.Semaphore,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Per-game enrichment under the semaphore: enrich → appdetails → progress."""
        async with sem:
            steam_id: int | None = None
            try:
                enriched = await self.enrich(game, session=session)
                raw = enriched.get("steam_appid")
                if isinstance(raw, int) and raw > 0:
                    steam_id = raw
            except Exception as e:
                logger.warning(
                    "[MetadataService] enrichment failed for %s: %s",
                    game.title, e,
                )
            if game.store != "steam":
                try:
                    await self.fetch_appdetails_for_game(
                        game, hint_steam_id=steam_id, session=session,
                    )
                except Exception as e:
                    logger.debug(
                        "[MetadataService] appdetails failed for %s: %s",
                        game.title, e,
                    )
            progress = self._sync_progress()
            if progress is not None:
                await progress.increment_steam(game.title)
                await progress.increment_unifidb(game.title)

    async def enrich(
        self, game: Game, session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any]:
        """Return enriched metadata for a single game."""
        cache_key = f"{game.store}:{game.store_game_id}"

        try:
            cached = self._cache.get(CACHE_NAMESPACE, cache_key)
            if isinstance(cached, dict):
                if cached.get("_negative"):
                    return {}
                if cached:
                    return cast("dict[str, Any]", cached)
        except Exception as e:
            logger.debug("[MetadataService] Cache read failed for %s: %s", cache_key, e)

        # Cache miss — fetch
        logger.debug("[MetadataService] Fetching metadata for %s", game.title)

        results = await asyncio.gather(
            metadata_sources.fetch_steam_store(
                game.title, config=self._config, session=session,
            ),
            metadata_sources.fetch_unifidb(game, config=self._config),
            return_exceptions=True,
        )

        steam_data = results[0] if isinstance(results[0], dict) else {}
        unifidb_data = results[1] if isinstance(results[1], dict) else {}

        # Merge (Steam > UnifiDB)
        merged: dict[str, Any] = {}
        merged.update(unifidb_data)
        merged.update(steam_data)

        try:
            payload = merged if merged else {"_negative": True}
            self._cache.set(CACHE_NAMESPACE, cache_key, payload)
        except Exception as e:
            logger.warning("[MetadataService] Failed to cache metadata for %s: %s", cache_key, e)

        return merged

    async def fetch_appdetails_for_game(
        self,
        game: Game,
        *,
        hint_steam_id: int | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a game to a real Steam AppID, fetch its rich appdetails."""
        from unifideck.steam.appdetails import fetch_appdetails
        steam_id = await self._resolve_steam_id(game, hint_steam_id, session=session)
        if steam_id is None:
            self._cache_set_safely(
                STEAM_REAL_APPID_NS, str(game.app_id), -1,
            )
            return None
        self._cache_set_safely(
            STEAM_REAL_APPID_NS, str(game.app_id), steam_id,
        )
        self._stamp_date_added(game.app_id)
        try:
            existing = self._cache.get(STEAM_METADATA_NS, str(steam_id))
            if isinstance(existing, dict):
                return cast("dict[str, Any]", existing)
        except Exception:
            logger.debug(
                "[MetadataService] metadata cache read failed", exc_info=True,
            )
        data = await fetch_appdetails(steam_id, config=self._config, session=session)
        if data is None:
            return None
        self._cache_set_safely(STEAM_METADATA_NS, str(steam_id), data)
        await self._fetch_reviews(steam_id, session=session)
        return data

    async def _fetch_reviews(
        self, steam_id: int, session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Fetch + cache the Steam review summary for ``steam_id`` once."""
        try:
            if self._cache.get(STEAM_REVIEWS_NS, str(steam_id)) is not None:
                return
        except Exception:
            logger.debug(
                "[MetadataService] reviews cache read failed", exc_info=True,
            )
        from unifideck.steam.appreviews import fetch_appreviews
        reviews = await fetch_appreviews(
            steam_id, config=self._config, session=session,
        )
        if reviews is not None:
            self._cache_set_safely(STEAM_REVIEWS_NS, str(steam_id), reviews)

    def _stamp_date_added(self, app_id: int) -> None:
        """Record a stable first-seen timestamp for the Date-Added sort."""
        try:
            if self._cache.get(SHORTCUT_ADDED_NS, str(app_id)) is not None:
                return
            self._cache_set_safely(
                SHORTCUT_ADDED_NS, str(app_id), int(time.time()),
            )
        except Exception:
            logger.debug(
                "[MetadataService] date-added stamp failed", exc_info=True,
            )

    async def _resolve_steam_id(
        self,
        game: Game,
        hint_steam_id: int | None,
        session: aiohttp.ClientSession | None = None,
    ) -> int | None:
        """Return a valid Steam AppID for ``game`` — hint or cache or live search."""
        if hint_steam_id is not None and hint_steam_id > 0:
            return hint_steam_id
        try:
            cached_id = self._cache.get(STEAM_REAL_APPID_NS, str(game.app_id))
            if isinstance(cached_id, int):
                return cached_id if cached_id > 0 else None
        except Exception:
            logger.debug(
                "[Metadata] cached appid read failed for %s", game.app_id,
                exc_info=True,
            )
        from unifideck.steam import library
        try:
            best = await library.search_store(
                game.title, config=self._config, session=session,
            )
        except Exception:
            logger.debug(
                "[Metadata] Steam search failed for %s", game.title,
            )
            return None
        if not best:
            return None
        raw = best.get("app_id")
        return raw if isinstance(raw, int) and raw > 0 else None

    def _cache_set_safely(
        self, namespace: str, key: str, value: Any,
    ) -> None:
        """``cache.set`` that logs (at DEBUG) on failure instead of raising."""
        try:
            self._cache.set(namespace, key, value)
        except Exception:
            logger.debug(
                "[Metadata] cache set %s failed for %s",
                namespace, key,
            )
