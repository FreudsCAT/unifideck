"""Artwork service orchestration.

OP-16a | py_modules/unifideck/services/artwork/service.py

``ArtworkService`` is the public API :

* ``ensure(game)`` — fetch missing artwork in the background, returns
  immediately;
* ``has(game)`` — disk-only check, no network;
* ``get_url(game, kind)`` — return the local file URL (or None);
* ``forget(game)`` — remove cached artwork for a game.

Backed by ``fetcher`` (network) + on-disk cache organised by SGDB
game id. Concurrency-controlled : at most N concurrent fetches to
avoid hammering SGDB on initial library load.
"""

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING
from ...core.cache_manager import CacheManager
from ...core.types import Events
from ...event_bus.event_bus import EventBus
from ...utils.config_helpers import get_cfg
from .event_handlers import _EventHandlersMixin
from .fetcher import download_and_save, find_artwork_url

if TYPE_CHECKING:
    from ...config import ConfigManager
logger = logging.getLogger(__name__)
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_FAILURE_COOLDOWN = 3600
DEFAULT_DOWNLOAD_TIMEOUT = 30
CACHE_NAMESPACE = "artwork_attempts"


class ArtworkService(_EventHandlersMixin):
    """Artwork service."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        grid_dir: str,
        api_key: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cache = cache
        self._grid_dir = grid_dir
        self._config = config
        self._api_key = api_key or get_cfg(
            config,
            "artwork.steamgriddb_api_key",
            "",
        )
        max_conc = int(
            get_cfg(
                config,
                "sync.artwork_concurrency",
                DEFAULT_MAX_CONCURRENT,
            )
        )
        self._semaphore = asyncio.Semaphore(max_conc)
        self._failure_cooldown = int(
            get_cfg(
                config,
                "artwork.failure_cooldown_seconds",
                DEFAULT_FAILURE_COOLDOWN,
            )
        )
        self._download_timeout = int(
            get_cfg(
                config,
                "artwork.download_timeout_seconds",
                DEFAULT_DOWNLOAD_TIMEOUT,
            )
        )
        self._cache.register(CACHE_NAMESPACE)
        from ...event_bus.event_bus_devex import auto_wire

        auto_wire(self, self._bus)
        logger.info(
            "[ArtworkService] wired (4 subscriptions, grid_dir=%s)",
            self._grid_dir,
        )

    async def stop(self) -> None:
        """Stop."""
        self._bus.off(Events.GAME_INSTALLED, self._on_game_installed)
        self._bus.off(Events.SYNC_COMPLETE, self._on_sync_complete)
        self._bus.off(Events.ARTWORK_REQUEST, self._on_artwork_request)
        self._bus.off(Events.SHORTCUT_CREATED, self._on_shortcut_created)

    async def fetch_artwork(
        self, app_id: int, store: str, game_id: str, title: str
    ) -> bool:
        """Fetch artwork."""
        async with self._semaphore:
            attempts = self._cache.get(CACHE_NAMESPACE, str(app_id)) or {}
            if attempts.get("failed_at"):
                import time as _t

                if _t.time() - attempts["failed_at"] < self._failure_cooldown:
                    return False
            any_saved = False
            for kind in ("grid", "hero", "logo", "icon"):
                url = await find_artwork_url(
                    title,
                    kind,
                    self._api_key,
                    self._config,
                )
                if not url:
                    continue
                saved = await download_and_save(
                    self._grid_dir,
                    app_id,
                    kind,
                    url,
                    self._download_timeout,
                )
                if saved:
                    any_saved = True
            if any_saved:
                attempts["last_success"] = True
                attempts.pop("failed_at", None)
            else:
                import time as _t

                attempts["failed_at"] = _t.time()
            self._cache.set(
                CACHE_NAMESPACE,
                str(app_id),
                attempts,
            )
            return any_saved
