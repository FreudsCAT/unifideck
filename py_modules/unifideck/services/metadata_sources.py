"""services/metadata_sources.py — third-party metadata source fetchers.

Stateless async fetchers split out of ``metadata_service.py`` (which had
crossed the 550-LOC volumetry cap). Each queries one external source
(Steam Store, UnifiDB, Metacritic) and returns a plain dict (``{}`` on any
failure), with no dependency on ``MetadataService`` state — the service's
``enrich`` and the Metacritic backfill pass both call these directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

    from unifideck.config import ConfigManager
    from unifideck.core.types import Game

logger = logging.getLogger(__name__)


async def fetch_steam_store(
    title: str,
    config: ConfigManager | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Search Steam Store API for the top match."""
    from unifideck.steam import library
    try:
        best = await library.search_store(title, config=config, session=session)
        if not best:
            return {}

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


async def fetch_unifidb(
    game: Game,
    config: ConfigManager | None = None,
) -> dict[str, Any]:
    """Query UnifiDB for canonical game info.

    Passes through the save-location block as well as the display fields.
    This projection used to keep only the five display keys, silently dropping
    ``save_locations`` / ``cloud`` / ``save_source`` — the Ludusavi+PCGamingWiki
    data that ``unifidb.game_to_cache_format`` deliberately preserves for
    downstream consumers. Because the drop happened one layer ABOVE the cache
    write, those fields never reached the metadata cache at all, which meant:

    * ``save_location_resolver`` lost its primary source and fell back to the
      wine-prefix title guesser (which is how a game's save path can resolve
      to the whole install folder);
    * ``_StatusMixin._cloud_supported`` always answered "unknown", so the
      cloud-save button could never dim for a game with no cloud support;
    * the App-Details panel could not report cloud-save availability before
      install.

    Kept to the consumed keys — ``platforms``/``external_ids`` are not read
    anywhere and would just inflate a cache with an entry per owned game.
    """
    from unifideck.metadata import unifidb
    try:
        result = await unifidb.lookup(
            game.store, game.store_game_id, game.title, config=config,
        )
        if not result:
            return {}

        out: dict[str, Any] = {
            "description": result.get("description"),
            "genres": result.get("genres", []),
            "developer": ", ".join(result.get("developers", [])) or None,
            "publisher": result.get("publisher"),
            "release_date": result.get("release_date"),
        }
        # Only when present: most catalog entries have no save data, and a
        # null key per game across a full library is pure cache weight.
        for field in ("save_locations", "cloud", "save_source"):
            value = result.get(field)
            if value:
                out[field] = value
        return out
    except Exception as e:
        logger.debug("[Metadata] UnifiDB fetch failed: %s", e)
        return {}


async def fetch_metacritic(
    title: str,
    config: ConfigManager | None = None,
) -> dict[str, Any]:
    """Fetch Metacritic critic + user score and summary."""
    from unifideck.metadata import metacritic
    try:
        result = await metacritic.fetch_score(title, config=config)
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
