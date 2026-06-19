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
    from unifideck.core.types import Game

logger = logging.getLogger(__name__)


async def fetch_steam_store(title: str) -> dict[str, Any]:
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


async def fetch_unifidb(game: Game) -> dict[str, Any]:
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


async def fetch_metacritic(title: str) -> dict[str, Any]:
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
