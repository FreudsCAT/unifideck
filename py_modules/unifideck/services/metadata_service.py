"""Metadata enrichment service — pull external metadata for owned games.

OP-12a | py_modules/unifideck/services/metadata_service.py

``MetadataService`` enriches the bare ``Game`` records emitted by
stores with metadata fetched from three external sources:

* **Steam Store** — exact title match → Steam app id + storefront
  metadata (genres, tags, etc.);
* **UniFiDB** — the project's curated database of cross-store
  identifiers and per-game corrections;
* **Metacritic** — review aggregate score.

The service subscribes to ``SYNC_COMPLETE`` on the bus, iterates
each game in the payload, and enriches them on demand. Results
are cached per ``(store, store_game_id)`` key with TTL configurable
through ``cache_ttl.steam_metadata`` and ``cache_ttl.metacritic_metadata``
(both default to 7 days).

The external lookups are best-effort: each fetch is wrapped in
``try/except`` so a network failure on one source doesn't block the
other two. The cached result may therefore be partial, which the UI
handles by hiding the missing fields rather than refusing to render
the game.
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
    """Enrich games with Steam Store / UniFiDB / Metacritic metadata."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        config: ConfigManager | None = None,
    ) -> None:
        """Wire the service to its dependencies and subscribe to the bus.

        Registers two cache namespaces with their respective TTLs
        (read from config if available, falling back to 7 days),
        then auto-wires every ``@subscribe``-decorated method on the
        instance.

        Args:
            bus: live event bus on which the service will subscribe
                to ``SYNC_COMPLETE`` to trigger enrichment.
            cache: shared cache manager. Two namespaces are
                registered: ``"metadata"`` for Steam / UniFiDB results
                and ``"metacritic"`` for the Metacritic score cache.
            config: optional config manager. Used to read the two
                cache TTL settings; ignored if absent or malformed.
        """
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
        """Unsubscribe from the bus on plugin shutdown.

        Removes the ``SYNC_COMPLETE`` subscription so the bus no
        longer holds a reference to this instance.
        """
        self._bus.off(Events.SYNC_COMPLETE, self._on_sync_complete)

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs) -> None:
        """Handle a ``SYNC_COMPLETE`` event by enriching each game.

        Triggered when a store finishes syncing its library. Iterates
        the ``games`` payload sequentially (one HTTP-heavy fetch at a
        time) and calls ``enrich`` on each — results land in the
        cache for the RPC layer to consume on the next library read.
        """
        games: list[Game] = kwargs.get("games", [])
        for game in games:
            await self.enrich(game)

    async def enrich(self, game: Game) -> dict[str, Any]:
        """Return cached metadata for ``game``, fetching it if absent.

        Cache key is ``"{store}:{store_game_id}"``. On a miss, queries
        the three external sources in sequence and stores the merged
        result. Each source's failure is silently dropped (logged at
        DEBUG): a missing Metacritic score is not a reason to refuse
        returning the Steam metadata.

        Args:
            game: the ``Game`` record to enrich.

        Returns:
            Dict with up to three keys (``steam``, ``unifidb``,
            ``metacritic``), plus ``steam_app_id`` when Steam data
            was found. May be partial.
        """
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
        """Search the Steam Store for ``title`` and return the top match.

        Wraps ``steam.library.search_store`` with a try/except so
        network failures, parser errors and rate limiting all
        degrade silently to ``None``.

        Args:
            title: game title to search for.

        Returns:
            Storefront metadata dict (app_id, genres, tags, …) or
            ``None`` on any failure.
        """
        try:
            from ..steam.library import search_store

            return await search_store(title, config=self._config)
        except Exception as e:
            logger.debug("[MetadataService] steam fetch: %s", e)
            return None

    async def _fetch_unifidb(self, game: Game) -> dict[str, Any] | None:
        """Look up the game in the UniFiDB curated database.

        UniFiDB is the project's curated catalog of cross-store
        identifiers and per-game corrections (e.g. canonical name
        across stores, known bad metadata fixes). The lookup is
        keyed on (store, store_game_id, title) for robustness when
        one of those fields is missing.

        Args:
            game: the ``Game`` record being enriched.

        Returns:
            UniFiDB entry dict, or ``None`` on miss or failure.
        """
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
        """Fetch the Metacritic score record for ``title``.

        Delegates to ``metadata.metacritic.fetch_score`` and unwraps
        the typed result into a plain dict for cache storage (the
        cache layer doesn't know about the typed dataclass).

        Args:
            title: game title to look up.

        Returns:
            Dict with ``critic_score``, ``user_score``, etc., or
            ``None`` on miss or failure.
        """
        try:
            from ..metadata.metacritic import fetch_score

            result = await fetch_score(title, config=self._config)
            return result.to_dict() if result else None
        except Exception as e:
            logger.debug("[MetadataService] metacritic fetch: %s", e)
            return None
