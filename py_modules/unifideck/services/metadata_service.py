"""Game metadata service — name normalisation + display-record helpers.

OP-12a | py_modules/unifideck/services/metadata_service.py

``MetadataService`` provides the cross-store helpers that build a
canonical view of a game from the partial information each store
provides :

* normalise game names (strip trademark glyphs, trailing edition
  suffixes, region codes) for sort + dedup;
* resolve cross-store duplicates (the same title owned on Epic and
  GOG should be a single entry in the UI, with both store badges);
* compose display metadata (full name, short name, sortable key)
  that downstream consumers (RPC mixins, artwork service) rely on.

Stateless and side-effect-free — every method is a pure function of
its inputs.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any, cast
from ..core.cache_manager import CacheManager
from ..core.types import Events, Game
from ..event_bus.event_bus import EventBus
from ..event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from ..config import ConfigManager
logger = logging.getLogger(__name__)
CACHE_NAMESPACE = "metadata"
DEFAULT_CACHE_TTL = 7 * 24 * 3600


class MetadataService:
    """Metadata service."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cache = cache
        self._config = config
        ttl = DEFAULT_CACHE_TTL
        if config is not None:
            try:
                ttl = int(config.get("cache_ttl.steam_metadata"))
            except (TypeError, ValueError):
                pass
        self._cache.register(CACHE_NAMESPACE, ttl_seconds=ttl)
        mc_ttl = ttl
        if config is not None:
            try:
                mc_ttl = int(config.get("cache_ttl.metacritic_metadata"))
            except (TypeError, ValueError):
                pass
        self._cache.register("metacritic", ttl_seconds=mc_ttl)
        from ..event_bus.event_bus_devex import auto_wire

        auto_wire(self, self._bus)
        logger.info("[MetadataService] wired (1 subscription)")

    async def stop(self) -> None:
        """Stop."""
        self._bus.off(Events.SYNC_COMPLETE, self._on_sync_complete)

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs) -> None:
        """On sync complete."""
        games: list[Game] = kwargs.get("games", [])
        for game in games:
            await self.enrich(game)

    async def enrich(self, game: Game) -> dict[str, Any]:
        """Enrich."""
        cache_key = f"{game.store}:{game.store_game_id}"
        cached = self._cache.get(CACHE_NAMESPACE, cache_key)
        if cached is not None:
            return cast("dict[str, Any]", cached)
        metadata: dict[str, Any] = {}
        steam_data = await self._fetch_steam_store(game.title)
        if steam_data:
            metadata["steam"] = steam_data
            metadata["steam_app_id"] = steam_data.get("app_id")
        unifidb_data = await self._fetch_unifidb(game)
        if unifidb_data:
            metadata["unifidb"] = unifidb_data
        metacritic_data = await self._fetch_metacritic(game.title)
        if metacritic_data:
            metadata["metacritic"] = metacritic_data
        self._cache.set(CACHE_NAMESPACE, cache_key, metadata)
        return metadata

    async def _fetch_steam_store(self, title: str) -> dict[str, Any] | None:
        """Fetch steam store."""
        try:
            from ..steam.library import search_store

            return await search_store(title, config=self._config)
        except Exception as e:
            logger.debug("[MetadataService] steam fetch: %s", e)
            return None

    async def _fetch_unifidb(self, game: Game) -> dict[str, Any] | None:
        """Fetch unifidb."""
        try:
            from ..metadata.unifidb import lookup

            return await lookup(
                game.store,
                game.store_game_id,
                game.title,
                config=self._config,
            )
        except Exception as e:
            logger.debug("[MetadataService] unifidb fetch: %s", e)
            return None

    async def _fetch_metacritic(self, title: str) -> dict[str, Any] | None:
        """Fetch metacritic."""
        try:
            from ..metadata.metacritic import fetch_score

            result = await fetch_score(title, config=self._config)
            return result.to_dict() if result else None
        except Exception as e:
            logger.debug("[MetadataService] metacritic fetch: %s", e)
            return None
