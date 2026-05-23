"""services/artwork/service.py — Game artwork fetcher.

EventBus subscriber that downloads game artwork from SteamGridDB
and writes files to Steam's grid/ directory so non-Steam
shortcuts display rich cover art. Subscribes to GAME_INSTALLED
(fetch newly-installed game) and SYNC_COMPLETE (bulk-fetch games
missing artwork). Concurrency capped via ``asyncio.Semaphore``
to stay under SGDB's rate limit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from unifideck.core.cache_manager import CacheManager
from unifideck.event_bus.event_bus import EventBus
from unifideck.event_bus.event_bus_devex import auto_wire

from .event_handlers import _EventHandlersMixin
from .fetcher import download_and_save, has_artwork
from .store_metadata import (
    fetch_store_urls,
    steam_cdn_urls,
    steam_search_appid,
)

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

# Tuning knobs — overridable via config.
DEFAULT_MAX_CONCURRENT = 4
# matches legacy + stays under SGDB 30/min free tier
DEFAULT_FAILURE_COOLDOWN = 3600
# 1 h skip after 404/parse failure
DEFAULT_DOWNLOAD_TIMEOUT = 30
# seconds for the image download

# Hardcoded SGDB API key inherited from staging (``main.py:2125``).
# Without an explicit ``artwork.steamgriddb_api_key`` config
# override, we use this so first-run installs get covers
# automatically — the staging behaviour every existing user is
# already trained on. Users who want their own key (e.g. to
# avoid sharing rate-limit quota) can set the config field.
_STAGING_SGDB_API_KEY = "1a410cb7c288b8f21016c2df4c81df74"

# Five canonical Steam-grid artwork kinds + their filename
# suffixes. The unsigned 32-bit AppID is prepended at write
# time (Steam expects unsigned in shortcuts.vdf and on disk).
_ARTWORK_KINDS = ("grid", "grid_l", "hero", "logo", "icon")

# Cache namespace for SGDB fetch attempts (success/failure tracking).
_CACHE_NAMESPACE = "sgdb_fetch"


class ArtworkService(_EventHandlersMixin):
    """SGDB artwork fetcher wired to the EventBus."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        grid_dir: str,
        api_key: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Store collaborators, initialize configs and semaphores."""
        self._bus = bus
        self._cache = cache
        self._grid_dir = grid_dir
        self._config = config

        # API key resolution order:
        #   1. constructor arg (explicit injection — tests)
        #   2. user config key ``artwork.steamgriddb_api_key``
        #   3. bundled staging fallback (so first-run installs work)
        self._api_key = api_key
        if self._config and not self._api_key:
            self._api_key = self._config.get("artwork.steamgriddb_api_key", "")
        if not self._api_key:
            self._api_key = _STAGING_SGDB_API_KEY

        max_concurrent = DEFAULT_MAX_CONCURRENT
        self._failure_cooldown = DEFAULT_FAILURE_COOLDOWN
        self._download_timeout = DEFAULT_DOWNLOAD_TIMEOUT

        if self._config:
            max_concurrent = self._config.get("artwork.max_concurrent", DEFAULT_MAX_CONCURRENT)
            self._failure_cooldown = self._config.get("artwork.failure_cooldown", DEFAULT_FAILURE_COOLDOWN)
            self._download_timeout = self._config.get("artwork.download_timeout", DEFAULT_DOWNLOAD_TIMEOUT)

        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Track pending tasks so we can wait for them on shutdown
        self._pending_tasks: set[asyncio.Task[Any]] = set()

        # We never run without an API key — the staging fallback
        # is bundled. Log the source so users can tell whether
        # they're on a custom key or the shared default.
        using_default = self._api_key == _STAGING_SGDB_API_KEY
        logger.info(
            "[ArtworkService] SteamGridDB API key configured (source: %s)",
            "shared default" if using_default else "user config",
        )

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` as if it were a bus
        # method, but ``auto_wire`` is module-level — the
        # call raised ``AttributeError`` and every
        # subscription was lost (caught and silenced upstream).
        auto_wire(self, self._bus)

    @property
    def grid_dir(self) -> str:
        return self._grid_dir

    async def stop(self) -> None:
        """Wait for any in-flight downloads to complete, release the semaphore."""
        self._bus.unsubscribe_all(self)

        if self._pending_tasks:
            logger.info("[ArtworkService] waiting for %d pending downloads", len(self._pending_tasks))
            # Best-effort wait for in-flight tasks
            _, pending = await asyncio.wait(
                self._pending_tasks,
                timeout=5.0,
                return_when=asyncio.ALL_COMPLETED,
            )
            for t in pending:
                t.cancel()

    async def fetch_artwork(
        self,
        app_id: int,
        store: str,
        game_id: str,
        title: str,
        force: bool = False,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        """Three-source pipeline that mirrors staging.

        Sources in priority order :

        1. **Per-store API** — authoritative box-art from the
           store the game actually lives on (GOG/Amazon
           ``gamesdb.gog.com`` ``vertical_cover``, Epic
           Legendary cache, Ubisoft GraphQL extras). Skipped:
           GOG/Amazon logos (thumbnail-quality) and all icons
           (no store has good ones).
        2. **SteamGridDB** — curated community art, with
           dimension-filtered grid queries (portrait 600x900
           / 660x930, landscape 920x430 / 460x215). The shared
           staging API key is bundled so first-run works.
        3. **Steam Store CDN** — last resort for matched
           AppIDs, hitting ``shared.steamstatic.com`` for
           ``library_600x900_2x``, ``header.jpg``,
           ``library_hero.jpg``, ``logo.png``.

        Returns ``{kind: bool}`` for the five Steam-grid kinds:
        ``grid`` (portrait), ``grid_l`` (landscape), ``hero``,
        ``logo``, ``icon``.  Per-game failure cooldown writes
        keep us out of SGDB rate-limit jail on dead titles.
        """
        result: dict[str, bool] = dict.fromkeys(_ARTWORK_KINDS, False)
        cache_key = f"{store}:{game_id}"
        if not force:
            last_attempt = self._cache.get(_CACHE_NAMESPACE, cache_key)
            if (
                last_attempt is not None
                and time.time() - float(last_attempt) < self._failure_cooldown
            ):
                logger.debug(
                    "[ArtworkService] skipping %s: in failure cooldown", title,
                )
                return result
            if await has_artwork(self._grid_dir, app_id):
                return dict.fromkeys(_ARTWORK_KINDS, True)

        current_task = asyncio.current_task()
        if current_task:
            self._pending_tasks.add(current_task)
            current_task.add_done_callback(self._pending_tasks.discard)

        async with self._semaphore:
            logger.info("[ArtworkService] fetching art for %s", title)
            sources: dict[str, str] = {}
            # Phase 1 — store metadata (authoritative).
            await self._fill_from_store(
                store, game_id, extras, app_id, result, sources,
            )
            # Phase 2 — SGDB fallback for any kind still missing.
            if not all(result.values()):
                await self._fill_from_sgdb(title, app_id, result, sources)
            # Phase 3 — Steam CDN last resort.
            if not all(result.values()):
                await self._fill_from_steam_cdn(
                    title, app_id, result, sources,
                )
            if not any(result.values()):
                self._cache.set(_CACHE_NAMESPACE, cache_key, time.time())
            else:
                self._log_sources(title, sources)
            return result

    async def _fill_from_store(
        self,
        store: str,
        game_id: str,
        extras: dict[str, Any] | None,
        app_id: int,
        result: dict[str, bool],
        sources: dict[str, str],
    ) -> None:
        """Phase 1: pull authoritative URLs from the per-store API."""
        try:
            urls = await fetch_store_urls(store, game_id, extras)
        except Exception as e:
            logger.debug("[ArtworkService] store metadata failed: %s", e)
            return
        # Staging policy: skip stores' logo for GOG/Amazon (thumbnail
        # quality) and skip icon for every store (no clean icons).
        if store in ("gog", "amazon"):
            urls.pop("logo", None)
        urls.pop("icon", None)
        await self._download_kinds(
            urls, app_id, result, sources, label=store.upper(),
        )

    async def _fill_from_sgdb(
        self,
        title: str,
        app_id: int,
        result: dict[str, bool],
        sources: dict[str, str],
    ) -> None:
        """Phase 2: batched SGDB lookup for everything still missing.

        Calls ``steamgriddb.fetch_all_kinds`` once per game — one
        title→game_id search followed by parallel asset fetches
        for every kind. Previous per-kind loop did 5 separate
        searches per game, blowing through the SGDB free-tier rate
        limit on large libraries. The new package also resolves
        ``grid_l`` natively with the landscape-dimension filter
        (the old single-kind helper had no way to distinguish
        portrait from landscape).
        """
        try:
            from unifideck.steam import steamgriddb
            urls = await steamgriddb.fetch_all_kinds(
                title, self._api_key, config=self._config,
            )
        except Exception as e:
            logger.debug("[ArtworkService] sgdb fetch failed (%s): %s", title, e)
            return
        for kind in _ARTWORK_KINDS:
            if result.get(kind):
                continue
            url = urls.get(kind)
            if not url:
                continue
            ok = await download_and_save(
                self._grid_dir, app_id, kind, url, self._download_timeout,
            )
            if ok:
                result[kind] = True
                sources[kind] = "SGDB"

    async def _fill_from_steam_cdn(
        self,
        title: str,
        app_id: int,
        result: dict[str, bool],
        sources: dict[str, str],
    ) -> None:
        """Phase 3: Steam Store CDN for any kind still missing.

        Resolves the title to a real Steam AppID (cached by
        ``MetadataService.fetch_appdetails_for_game`` when
        available, otherwise live-searched here), then pulls
        the canonical ``shared.steamstatic.com`` URLs from
        :func:`steam_cdn_urls`.
        """
        steam_id = self._lookup_cached_steam_id(app_id)
        if steam_id is None:
            try:
                steam_id = await steam_search_appid(title)
            except Exception:
                steam_id = None
        if not steam_id:
            return
        urls = steam_cdn_urls(steam_id)
        await self._download_kinds(
            urls, app_id, result, sources, label="STEAM",
        )

    def _lookup_cached_steam_id(self, app_id: int) -> int | None:
        """Read the precomputed shortcut-AppID → Steam-AppID mapping."""
        try:
            stores = getattr(self._cache, "_stores", None)
            if not isinstance(stores, dict):
                return None
            data = getattr(stores.get("steam_real_appid"), "_data", None)
            if not isinstance(data, dict):
                return None
            value = data.get(str(app_id))
            return value if isinstance(value, int) and value > 0 else None
        except Exception:
            return None

    async def _download_kinds(
        self,
        urls: dict[str, str],
        app_id: int,
        result: dict[str, bool],
        sources: dict[str, str],
        *,
        label: str,
    ) -> None:
        """Download every URL in ``urls`` that fills a missing kind.

        Mutates ``result`` and ``sources`` in place.  Each download
        is awaited sequentially to stay polite with the upstream
        CDN; the outer semaphore already caps cross-game
        parallelism.
        """
        for kind, url in urls.items():
            if kind not in _ARTWORK_KINDS or result.get(kind):
                continue
            if not url:
                continue
            ok = await download_and_save(
                self._grid_dir, app_id, kind, url, self._download_timeout,
            )
            if ok:
                result[kind] = True
                sources[kind] = label

    def _log_sources(self, title: str, sources: dict[str, str]) -> None:
        """Emit a single-line summary of where each kind came from."""
        by_source: dict[str, list[str]] = {}
        for kind, src in sources.items():
            by_source.setdefault(src, []).append(kind)
        summary = " ".join(
            f"{src}:{'+'.join(sorted(kinds))}"
            for src, kinds in by_source.items()
        )
        logger.info("[ArtworkService] %s → %s", title, summary)
