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

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """Enrich all games from the latest sync."""
        games = kwargs.get("games", [])
        if not games:
            return

        logger.info("[MetadataService] Starting background enrichment for %d games", len(games))

        for game in games:
            try:
                # Fire and forget enrichment task for each game so one slow API doesn't block
                await self.enrich(game)
            except Exception as e:
                logger.warning("[MetadataService] Enrichment failed for %s: %s", game.title, e)

    async def enrich(self, game: Game) -> dict[str, Any]:
        """Return enriched metadata for a single game."""
        cache_key = f"{game.store}:{game.store_game_id}"

        try:
            cached = self._cache.get(CACHE_NAMESPACE, cache_key)
            if cached and isinstance(cached, dict):
                # Simple TTL check could be implemented if cache returns timestamps
                # Assuming CacheManager handles TTL or we trust it for now
                # ``cache.get`` is typed Any — the isinstance narrowing
                # makes this a real dict at runtime, anchor the type
                # for mypy via cast.
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
        merged = {}
        merged.update(metacritic_data)
        merged.update(unifidb_data)
        merged.update(steam_data)

        if merged:
            try:
                # TTL is configured at register time in
                # ``bootstrap/cache_registry.py`` (7 days for the
                # ``"metadata"`` slot). ``CacheManager.set`` takes
                # only ``(cache, key, value)`` — earlier this site
                # also passed ``ttl=self._ttl`` and silently raised
                # ``TypeError: set() got an unexpected keyword
                # argument 'ttl'`` on every cache write.
                self._cache.set(CACHE_NAMESPACE, cache_key, merged)
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
            return {
                "steam_appid": best.get("appid"),
                "title": best.get("name"),
                "release_date": best.get("released"),
                "header_image": best.get("header_url"),
                "is_free": best.get("is_free"),
            }
        except Exception as e:
            logger.debug("[Metadata] Steam fetch failed for %s: %s", title, e)
            return {}

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
