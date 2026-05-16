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
from .fetcher import download_and_save, find_artwork_url, has_artwork

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

        self._api_key = api_key
        if self._config and not self._api_key:
            self._api_key = self._config.get("artwork.steamgriddb_api_key", "")

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

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` as if it were a bus
        # method, but ``auto_wire`` is module-level — the
        # call raised ``AttributeError`` and every
        # subscription was lost (caught and silenced upstream).
        auto_wire(self, self._bus)

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
    ) -> dict[str, bool]:
        """Fetch and save 4 artwork types (grid, hero, logo, icon).

        Skips when the (store, game_id) is in the failure cooldown
        cache. Acquires the concurrency semaphore before hitting
        SGDB. For each artwork type, resolves the URL via
        ``find_artwork_url``, downloads via ``download_and_save``
        with per-request timeout, writes to ``grid_dir`` under
        Steam's expected naming. Returns
        ``{grid: bool, hero: bool, logo: bool, icon: bool}``
        flagging which succeeded. On SGDB 404, records the
        failure in cache so the next cycle skips this game.
        """
        result = {"grid": False, "hero": False, "logo": False, "icon": False}

        if not self._api_key:
            logger.debug("[ArtworkService] skipping %s: no SGDB API key", title)
            return result

        cache_key = f"{store}:{game_id}"

        if not force:
            # Check if we recently failed
            last_attempt = self._cache.get(_CACHE_NAMESPACE, cache_key)
            if (
                last_attempt is not None
                and time.time() - float(last_attempt) < self._failure_cooldown
            ):
                logger.debug(
                    "[ArtworkService] skipping %s: in failure cooldown", title,
                )
                return result

            # Check if we already have the essential art
            if await has_artwork(self._grid_dir, app_id):
                return {"grid": True, "hero": True, "logo": True, "icon": True}

        # Track the task so we can await it on shutdown
        current_task = asyncio.current_task()
        if current_task:
            self._pending_tasks.add(current_task)
            current_task.add_done_callback(self._pending_tasks.discard)

        async with self._semaphore:
            logger.info("[ArtworkService] fetching art for %s", title)

            for kind in ("grid", "hero", "logo", "icon"):
                url = await find_artwork_url(title, kind, self._api_key, self._config)
                if url:
                    success = await download_and_save(
                        self._grid_dir,
                        app_id,
                        kind,
                        url,
                        self._download_timeout,
                    )
                    result[kind] = success

            # Cache a complete miss so we don't hammer SGDB on subsequent
            # lookups. Partial misses (some kinds succeeded) are NOT cached
            # because the caller may want to retry the failed kinds.
            if not any(result.values()):
                self._cache.set(_CACHE_NAMESPACE, cache_key, time.time())

            return result
