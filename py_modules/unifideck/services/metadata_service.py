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
            # Own the metadata phase total. Previously
            # ``_on_post_sync_phase`` in SyncService set this
            # using the *artwork* count, which is wrong — the
            # metadata loop walks every game, not just games
            # that needed artwork. ``start_metadata`` also sets
            # ``steam_total``, ``unifidb_total``, ``metacritic_total``
            # to ``total`` so the three per-source progress rows
            # have a real denominator.
            progress = self._bus.get_sync_progress() if hasattr(self._bus, "get_sync_progress") else None
            if progress is not None:
                progress.start_metadata(total)
            logger.info(
                "[MetadataService] background enrichment started for %d games",
                total,
            )
            every = max(1, min(50, total // 5))
            for done, game in enumerate(games, start=1):
                # Cancel checkpoint — SyncService.cancel() flips
                # progress.status to "cancelled"; bail out to the
                # ``finally`` so POST_SYNC_PHASE_CHANGED still fires
                # and ``_post_sync_pending`` clears (without it the
                # bar would stall at the metadata phase indefinitely).
                if progress is not None and progress.status == "cancelled":
                    logger.info(
                        "[MetadataService] cancel detected at %d/%d — aborting",
                        done, total,
                    )
                    break
                try:
                    await self.enrich(game)
                except Exception as e:
                    logger.warning(
                        "[MetadataService] enrichment failed for %s: %s",
                        game.title, e,
                    )
                if game.store != "steam":
                    try:
                        await self.fetch_appdetails_for_game(game)
                    except Exception as e:
                        logger.debug(
                            "[MetadataService] appdetails failed for %s: %s",
                            game.title, e,
                        )
                # Tick each per-source counter once per game. The
                # three sources run in parallel inside ``enrich``
                # via ``asyncio.gather`` — at this point all three
                # have either succeeded or failed for this game,
                # so the increment represents "attempt complete"
                # rather than "data fetched". The frontend renders
                # one row per source.
                progress = self._bus.get_sync_progress() if hasattr(self._bus, "get_sync_progress") else None
                if progress is not None:
                    await progress.increment_steam(game.title)
                    await progress.increment_unifidb(game.title)
                    await progress.increment_metacritic(game.title)
                if done % every == 0:
                    logger.info(
                        "[MetadataService] progress: %d/%d enriched",
                        done, total,
                    )
        except asyncio.CancelledError:
            cancelled_by_replace = True
            logger.info(
                "[MetadataService] enrichment cancelled — newer sync took over",
            )
            raise
        finally:
            if not cancelled_by_replace:
                await self._bus.emit(
                    Events.POST_SYNC_PHASE_CHANGED,
                    phase="metadata", active=False,
                    total=total, done=total,
                )
            logger.info(
                "[MetadataService] background enrichment finished (%d games)",
                total,
            )

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

        # Parallel fetch from sources
        results = await asyncio.gather(
            self._fetch_steam_store(game.title),
            self._fetch_unifidb(game),
            self._fetch_metacritic(game.title),
            return_exceptions=True
        )

        steam_data = results[0] if isinstance(results[0], dict) else {}
        unifidb_data = results[1] if isinstance(results[1], dict) else {}
        metacritic_data = results[2] if isinstance(results[2], dict) else {}

        # Merge (Steam > UnifiDB > Metacritic)
        merged: dict[str, Any] = {}
        merged.update(metacritic_data)
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
        self, game: Game,
    ) -> dict[str, Any] | None:
        """Resolve a game to a real Steam AppID, fetch its rich appdetails.

        Two-step lookup that powers the frontend's
        ``SteamStorePatcher``:

        1. ``search_store(game.title)`` returns the best-match Steam
           AppID (or ``None`` for niche / non-Steam-store titles).
        2. ``appdetails.fetch_appdetails(steam_id)`` returns the
           full Steam-Store JSON for that AppID.

        Both results are cached:

        * ``steam_real_appid`` namespace, key = the Unifideck
          shortcut AppID — the mapping the frontend reads via
          ``get_real_steam_appid_mappings``.
        * ``steam_metadata`` namespace, key = ``str(steam_app_id)``
          — the rich payload the frontend reads via
          ``get_steam_metadata_cache``.

        Args:
            game: a ``Game`` produced by ``SyncService``. Must
                have ``app_id`` set (computed during sync) — that's
                the synthetic shortcut id the frontend looks up.

        Returns:
            The rich ``appdetails`` dict on success, ``None`` on no
            match or upstream failure.
        """
        from unifideck.steam import library
        from unifideck.steam.appdetails import fetch_appdetails

        if game.app_id is None:
            return None
        try:
            best = await library.search_store(game.title)
        except Exception:
            logger.debug(
                "[Metadata] Steam search failed for %s", game.title,
            )
            return None
        if not best:
            return None
        steam_id_raw = best.get("app_id")
        if not isinstance(steam_id_raw, int) or steam_id_raw <= 0:
            return None
        steam_id = steam_id_raw
        # Record the shortcut → real-Steam-id mapping so the
        # frontend RPC ``get_real_steam_appid_mappings`` can
        # return it without re-running the search.
        try:
            self._cache.set(
                STEAM_REAL_APPID_NS, str(game.app_id), steam_id,
            )
        except Exception:
            logger.debug(
                "[Metadata] cache set %s failed for shortcut %s",
                STEAM_REAL_APPID_NS, game.app_id,
            )
        # Honour any already-cached appdetails payload — Steam
        # data rarely changes within a TTL window and the
        # endpoint is rate-limited (~200 reqs / 5 min upstream).
        try:
            existing = self._cache.get(STEAM_METADATA_NS, str(steam_id))
            if isinstance(existing, dict):
                return cast("dict[str, Any]", existing)
        except Exception:
            existing = None
        data = await fetch_appdetails(steam_id, config=self._config)
        if data is None:
            return None
        try:
            self._cache.set(STEAM_METADATA_NS, str(steam_id), data)
        except Exception:
            logger.debug(
                "[Metadata] cache set %s failed for steam id %d",
                STEAM_METADATA_NS, steam_id,
            )
        return data

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
