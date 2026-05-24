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
from typing import TYPE_CHECKING, Any, cast

from unifideck.core.types import Game
from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe
from unifideck.services import metadata_backfill

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

    async def _run_enrichment(self, games: list[Game]) -> None:
        """Background enrichment loop. ``finally`` emits
        ``POST_SYNC_PHASE_CHANGED(active=False)`` so the sync's
        post-phase tracker advances on success, exception, or
        user-initiated sync cancellation. Without the guard, an
        empty game list or any uncaught error left the progress
        bar stuck.

        Exception: when the task is cancelled by a newer
        ``SYNC_COMPLETE`` handler (because another sync started
        before this enrichment finished), the emit is skipped —
        the new run owns the metadata phase and emitting here
        would mark it done before the new run has actually
        processed any games.
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
            sem = asyncio.Semaphore(ENRICHMENT_CONCURRENCY)
            tasks = [
                asyncio.create_task(self._enrich_one_game(g, sem))
                for g in games
            ]
            await self._drain_enrichment(tasks, progress, total)
        except asyncio.CancelledError:
            cancelled_by_replace = True
            logger.info(
                "[MetadataService] enrichment cancelled — newer sync took over",
            )
            raise
        finally:
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
            # Long-tail Metacritic lookups: fire-and-forget, runs
            # after the phase-done emit so the progress bar isn't
            # gated on it. See ``metadata_backfill``.
            if not cancelled_by_replace:
                metadata_backfill.spawn(self, games)

    def _sync_progress(self) -> Any:
        """Return the bus's ``SyncProgress`` tracker, or ``None``."""
        if not hasattr(self._bus, "get_sync_progress"):
            return None
        return self._bus.get_sync_progress()

    async def _drain_enrichment(
        self, tasks: list[asyncio.Task[None]], progress: Any, total: int,
    ) -> None:
        """Await every per-game task as it finishes, logging progress.

        Extracted from ``_run_enrichment`` to keep that function
        under the 80-LOC / 4-nesting volumetry caps. Honours the
        ``progress.status == "cancelled"`` flank by cancelling
        in-flight tasks and exiting early.
        """
        every = max(1, min(50, total // 5))
        done_count = 0
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
            except Exception:  # noqa: BLE001 — per-game errors are best-effort
                pass
            done_count += 1
            if done_count % every == 0:
                logger.info(
                    "[MetadataService] progress: %d/%d enriched",
                    done_count, total,
                )

    async def _enrich_one_game(
        self, game: Game, sem: asyncio.Semaphore,
    ) -> None:
        """Per-game enrichment under the semaphore: enrich → appdetails → progress.

        Reuses the ``steam_appid`` resolved inside ``enrich`` to skip
        the duplicate ``search_store`` call that
        ``fetch_appdetails_for_game`` would otherwise issue.
        """
        async with sem:
            steam_id: int | None = None
            try:
                enriched = await self.enrich(game)
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
                        game, hint_steam_id=steam_id,
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
                # Metacritic increment intentionally absent: the
                # actual ``_fetch_metacritic`` work moved to the
                # post-phase backfill (see metadata_backfill.spawn).
                # Ticking here would advance a counter for work
                # that hasn't started yet.

    async def enrich(self, game: Game) -> dict[str, Any]:
        """Return enriched metadata for a single game.

        Caches both positive and negative results — a previous miss
        is stored with the ``{"_negative": True}`` sentinel so the
        next sync skips the three API calls for known-not-found
        titles. Without negative caching, every sync re-queries
        Steam Store + UnifiDB + Metacritic for the same niche games
        that none of them have, wasting bandwidth and tripping rate
        limits on large libraries.
        """
        cache_key = f"{game.store}:{game.store_game_id}"

        try:
            cached = self._cache.get(CACHE_NAMESPACE, cache_key)
            if isinstance(cached, dict):
                # Negative sentinel: previously confirmed no source
                # had data for this game. Return empty so callers
                # behave as if no metadata is available, without
                # re-hitting the network.
                if cached.get("_negative"):
                    return {}
                if cached:
                    # ``cache.get`` is typed Any — the isinstance
                    # narrowing makes this a real dict at runtime;
                    # anchor the type for mypy via cast.
                    return cast("dict[str, Any]", cached)
        except Exception as e:
            logger.debug("[MetadataService] Cache read failed for %s: %s", cache_key, e)

        # Cache miss — fetch
        logger.debug("[MetadataService] Fetching metadata for %s", game.title)

        # Parallel fetch from sources. Metacritic is no longer in
        # this gather — most games already get a metacritic.score
        # from Steam's appdetails payload (fetched by
        # ``fetch_appdetails_for_game``), and the long-tail
        # remainder is filled in by ``_metacritic_backfill`` after
        # the phase-done emit, so the user's progress bar isn't
        # gated on the slowest source.
        results = await asyncio.gather(
            self._fetch_steam_store(game.title),
            self._fetch_unifidb(game),
            return_exceptions=True,
        )

        steam_data = results[0] if isinstance(results[0], dict) else {}
        unifidb_data = results[1] if isinstance(results[1], dict) else {}

        # Merge (Steam > UnifiDB)
        merged: dict[str, Any] = {}
        merged.update(unifidb_data)
        merged.update(steam_data)

        try:
            # TTL is configured at register time in
            # ``bootstrap/cache_registry.py`` (7 days for the
            # ``"metadata"`` slot). ``CacheManager.set`` takes
            # only ``(cache, key, value)`` — earlier this site
            # also passed ``ttl=self._ttl`` and silently raised
            # ``TypeError: set() got an unexpected keyword
            # argument 'ttl'`` on every cache write.
            #
            # Empty result → store negative sentinel so the next
            # sync skips the three API calls. Sharing the same TTL
            # as positive entries is fine — a game that didn't have
            # metadata last week probably still doesn't, and if it
            # does the TTL expiry kicks in eventually.
            payload = merged if merged else {"_negative": True}
            self._cache.set(CACHE_NAMESPACE, cache_key, payload)
        except Exception as e:
            logger.warning("[MetadataService] Failed to cache metadata for %s: %s", cache_key, e)

        return merged

    async def _fetch_steam_store(self, title: str) -> dict[str, Any]:
        """Search Steam Store API for the top match.

        Drift fix (2026-05-15): ``library.search_store`` returns
        ``dict[str, Any] | None`` (the best single match), not a
        list. The previous body indexed it with ``results[0]``
        which would either index a dict-by-int (TypeError) or
        crash. Treating it as a single dict throughout.
        """
        from unifideck.steam import library
        try:
            best = await library.search_store(title)
            if not best:
                return {}

            # The exact key names depend on the Steam Store API
            # response shape; we forward what's present and leave
            # absent fields as ``None`` so downstream callers can
            # detect missing data instead of seeing wrong values.
            #
            # ``library.search_store`` returns ``app_id`` (snake_case)
            # in its result dict — see :class:`SteamStoreResult`.
            # The earlier ``best.get("appid")`` returned ``None``
            # so ``steam_appid`` was always absent.
            return {
                "steam_appid": best.get("app_id"),
                "title": best.get("name"),
                "release_date": best.get("release_date"),
                "header_image": best.get("header_image"),
                "is_free": False,
            }
        except Exception as e:
            logger.debug("[Metadata] Steam fetch failed for %s: %s", title, e)
            return {}

    async def fetch_appdetails_for_game(
        self, game: Game, *, hint_steam_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a game to a real Steam AppID, fetch its rich appdetails.

        Powers the frontend's ``SteamStorePatcher`` via two caches:
        ``steam_real_appid`` (shortcut → Steam AppID) and
        ``steam_metadata`` (full appdetails per Steam AppID).
        ``hint_steam_id`` lets callers skip the duplicate
        ``search_store`` call when they already resolved it via
        ``enrich`` — passing ``None`` keeps the original ad-hoc
        behaviour for callers outside the sync loop.
        """
        from unifideck.steam.appdetails import fetch_appdetails
        if game.app_id is None:
            return None
        steam_id = await self._resolve_steam_id(game, hint_steam_id)
        if steam_id is None:
            return None
        self._cache_set_safely(
            STEAM_REAL_APPID_NS, str(game.app_id), steam_id,
        )
        try:
            existing = self._cache.get(STEAM_METADATA_NS, str(steam_id))
            if isinstance(existing, dict):
                return cast("dict[str, Any]", existing)
        except Exception:
            pass
        data = await fetch_appdetails(steam_id, config=self._config)
        if data is None:
            return None
        self._cache_set_safely(STEAM_METADATA_NS, str(steam_id), data)
        return data

    async def _resolve_steam_id(
        self, game: Game, hint_steam_id: int | None,
    ) -> int | None:
        """Return a valid Steam AppID for ``game`` — hint or live search."""
        if hint_steam_id is not None and hint_steam_id > 0:
            return hint_steam_id
        from unifideck.steam import library
        try:
            best = await library.search_store(game.title)
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

    async def _fetch_unifidb(self, game: Game) -> dict[str, Any]:
        """Query UnifiDB for canonical game info.

        Drift fix (2026-05-15): the previous body called
        ``unifidb.fetch_game(store, id, title)`` and expected a
        dataclass with attributes ``unifidb_id``, ``description``,
        ``genres``, ``developer``, ``publisher``, ``release_date``.
        None of that matches what ``unifidb`` actually exposes —
        the real entry-point is ``lookup(store, game_id, title)``
        which returns ``dict[str, Any] | None`` keyed on
        ``title``, ``description``, ``release_date``, ``publisher``,
        ``developers`` (plural list), ``genres``.

        Treating ``game`` as a ``Game`` dataclass (attribute
        access, not ``.get(...)``).
        """
        from unifideck.metadata import unifidb
        try:
            result = await unifidb.lookup(
                game.store, game.store_game_id, game.title,
            )
            if not result:
                return {}

            return {
                # Pick whatever the UnifiDB record has; missing
                # keys land as ``None`` so the downstream cache
                # doesn't store partial-but-incorrect data.
                "description": result.get("description"),
                "genres": result.get("genres", []),
                # Note: UnifiDB exposes ``developers`` (plural list);
                # collapse to a comma-joined string for display
                # parity with other sources.
                "developer": ", ".join(result.get("developers", [])) or None,
                "publisher": result.get("publisher"),
                "release_date": result.get("release_date"),
            }
        except Exception as e:
            logger.debug("[Metadata] UnifiDB fetch failed: %s", e)
            return {}

    async def _fetch_metacritic(self, title: str) -> dict[str, Any]:
        """Fetch Metacritic critic + user score and summary.

        Drift fix (2026-05-15): the previous body referenced
        ``critic_score`` and ``summary`` — neither attribute
        exists on ``MetacriticScore``. The real attributes are
        ``metascore`` (the critic score) and ``description``
        (the editorial blurb).
        """
        from unifideck.metadata import metacritic
        try:
            result = await metacritic.fetch_score(title)
            if not result:
                return {}

            return {
                "metacritic_score": result.metascore,
                "metacritic_user_score": result.user_score,
                "metacritic_url": result.url,
                "summary": result.description,
            }
        except Exception as e:
            logger.debug("[Metadata] Metacritic fetch failed for %s: %s", title, e)
            return {}
