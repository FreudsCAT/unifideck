"""Artwork service — fetch & cache SteamGridDB artwork for Steam shortcuts.

OP-16a | py_modules/unifideck/services/artwork/service.py

``ArtworkService`` listens to bus events that signal "a Steam
shortcut now exists for this game" (``GAME_INSTALLED``,
``SHORTCUT_CREATED``, ``SYNC_COMPLETE``, ``ARTWORK_REQUEST``) and
fetches the four artwork variants Steam expects (``grid``, ``hero``,
``logo``, ``icon``) from SteamGridDB, saving them under the Steam
``grid/`` directory so Steam picks them up automatically on next
library refresh.

Behaviour highlights:

* **Concurrency capped** by a semaphore (default 4 concurrent
  fetches) to avoid hammering SteamGridDB on initial library boots
  with dozens of games.
* **Failure cooldown** of 1 h: after a failed fetch, the same
  app_id is not retried for ``failure_cooldown_seconds`` to avoid
  log spam and rate-limit pressure.
* **Per-attempt state** is recorded in a cache namespace
  (``artwork_attempts``) keyed by app_id, holding ``failed_at`` or
  ``last_success``.
* **Download timeout** is enforced per file (default 30 s).

The service composes ``_EventHandlersMixin`` for the four
event-handler methods, keeping the orchestration logic isolated
from event wiring.
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
    """Fetch SteamGridDB artwork and write it into Steam's grid dir.

    Concurrency-controlled (semaphore-bounded) so initial library
    syncs can't open hundreds of HTTP connections to SteamGridDB at
    once. Per-game failure cooldowns prevent repeated retries on
    games that have no SGDB match (avoiding wasted network traffic
    and rate-limit hits).
    """

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        grid_dir: str,
        api_key: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Wire the service to its dependencies and load tunables.

        Reads the SGDB API key, concurrency, cooldown and download
        timeout from the config (with hard-coded defaults), then
        registers the ``artwork_attempts`` cache namespace and
        auto-wires the four event handlers from
        ``_EventHandlersMixin``.

        Args:
            bus: live event bus.
            cache: shared cache manager. The
                ``artwork_attempts`` namespace stores per-game
                ``last_success`` flags and ``failed_at`` timestamps
                for the cooldown logic.
            grid_dir: absolute path to Steam's
                ``userdata/0/config/grid`` directory where the
                fetched files are written.
            api_key: optional SteamGridDB API key override. When
                absent, falls back to
                ``artwork.steamgriddb_api_key`` from the config.
            config: optional config manager. Tunables read:
                ``sync.artwork_concurrency`` (4),
                ``artwork.failure_cooldown_seconds`` (3600),
                ``artwork.download_timeout_seconds`` (30).
        """
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
        """Unsubscribe every bus handler on plugin shutdown.

        Removes the four subscriptions (``GAME_INSTALLED``,
        ``SYNC_COMPLETE``, ``ARTWORK_REQUEST``, ``SHORTCUT_CREATED``)
        so the bus no longer holds references to this instance.
        """
        self._bus.off(Events.GAME_INSTALLED, self._on_game_installed)
        self._bus.off(Events.SYNC_COMPLETE, self._on_sync_complete)
        self._bus.off(Events.ARTWORK_REQUEST, self._on_artwork_request)
        self._bus.off(Events.SHORTCUT_CREATED, self._on_shortcut_created)

    async def fetch_artwork(
        self, app_id: int, store: str, game_id: str, title: str
    ) -> bool:
        """Fetch and save all four artwork kinds for one app_id.

        Acquires the concurrency semaphore (capped by
        ``sync.artwork_concurrency``), checks the per-game cooldown
        (skip if the last attempt failed less than
        ``failure_cooldown`` seconds ago), and iterates the four
        artwork kinds (``grid`` / ``hero`` / ``logo`` / ``icon``)
        sequentially. Each kind queries SGDB independently — partial
        success (e.g. grid + hero found but no logo) is the normal
        outcome and treated as a successful fetch overall.

        Updates the ``artwork_attempts`` cache entry with either
        ``last_success: True`` (and clears ``failed_at``) or
        ``failed_at: <timestamp>`` for the cooldown logic.

        Args:
            app_id: Steam app id (the file naming key under
                ``grid_dir``).
            store: store identifier (currently unused but logged
                for debugging).
            game_id: store-specific game id (currently unused but
                logged for debugging).
            title: game title used as the SGDB search query.

        Returns:
            ``True`` if at least one artwork kind was successfully
            saved, ``False`` if every fetch failed or every URL
            lookup returned ``None``.
        """
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
