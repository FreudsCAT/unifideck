"""Thin SteamGridDB orchestrator + the module-level free functions.

Three responsibilities:

* :class:`SteamGridDBClient` — instance wrapper for callers that want
  to share an API key across calls (used by the Ubisoft store + the
  Ubisoft auth facade).
* :func:`search_artwork` — single-kind helper (returns a URL or
  ``None``). The legacy entry-point services/artwork still calls.
* :func:`fetch_all_kinds` — all-kinds helper (returns ``{kind: url}``).
  Replaces the old loop-five-times-per-game pattern.

This file is intentionally short — the real work lives in
:mod:`search`, :mod:`assets`, :mod:`ranking`, :mod:`batch`. Anything
beyond glue belongs in one of those.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.utils.config_helpers import get_cfg

from . import batch as _batch
from .assets import ArtworkAsset, fetch_with_fallback
from .constants import ARTWORK_KINDS, SGDB_API_BASE
from .ranking import pick_best
from .search import search_game_id

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)


def _resolve_base(config: ConfigManager | None) -> str:
    """Read ``artwork.steamgriddb_api_base`` or fall back to default."""
    # ``get_cfg`` is typed ``Any``; coerce so mypy sees the real shape.
    return str(get_cfg(config, "artwork.steamgriddb_api_base", SGDB_API_BASE))


def _resolve_timeout(config: ConfigManager | None) -> int:
    """Read ``artwork.download_timeout_seconds`` or fall back to 30."""
    return int(get_cfg(config, "artwork.download_timeout_seconds", 30))


def _ssl_free_session() -> Any:
    """``aiohttp.ClientSession`` with TLS verification disabled.

    The Steam Deck's system CA store is frequently stale, so HTTPS to
    ``www.steamgriddb.com`` can fail certificate verification inside the
    Decky plugin process. Every other HTTP path in the artwork pipeline
    — store metadata and the image download in
    ``services/artwork`` — already opts out via
    ``TCPConnector(ssl=False)``; the SGDB *API* session was the lone
    exception, and the swallowed ``SSLCertVerificationError`` made every
    search + asset call return empty, so SGDB contributed *zero* covers
    to the whole library (icons, which only come from SGDB, were the
    most visible casualty).
    """
    import aiohttp

    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))


async def search_artwork(
    title: str,
    kind: str,
    api_key: str | None = None,
    config: ConfigManager | None = None,
) -> str | None:
    """Resolve a title to one URL for a single kind.

    Legacy single-kind entry-point. Used by
    ``services/artwork/fetcher.py::find_artwork_url``. New callers
    should prefer :func:`fetch_all_kinds` — one search instead of
    five for the full set.

    Returns ``None`` for missing/invalid kind, missing api key, no
    match, or any HTTP failure (always non-raising — caller chains
    into Steam-CDN fallback without exception handling).
    """
    if kind not in ARTWORK_KINDS:
        raise ValueError(f"unknown artwork kind: {kind}")
    if not api_key:
        return None
    base = _resolve_base(config)
    timeout = _resolve_timeout(config)
    async with _ssl_free_session() as session:
        game_id = await search_game_id(
            session, base, api_key, title, timeout_sec=timeout,
        )
        if game_id is None:
            return None
        assets = await fetch_with_fallback(
            session, base, api_key, game_id, kind, timeout_sec=timeout,
        )
    best = pick_best(assets)
    return best.url if best else None


async def fetch_all_kinds(
    title: str,
    api_key: str | None,
    config: ConfigManager | None = None,
    only_kinds: frozenset[str] | None = None,
) -> dict[str, str | None]:
    """Resolve a title to URLs per kind (one search + parallel fetch).

    Args:
        only_kinds: when set, resolve only these kinds (e.g.
            ``frozenset({"icon"})`` to backfill just the gap a previous
            sync missed). ``None`` means all five.

    Returns ``{kind: url | None}`` for every requested kind. ``None``
    means "no match" or "search failed" — caller treats both the same.
    Without an API key, returns all-None without making any HTTP
    calls (matches old behaviour).
    """
    if not api_key:
        return dict.fromkeys(only_kinds or ARTWORK_KINDS)
    base = _resolve_base(config)
    timeout = _resolve_timeout(config)
    async with _ssl_free_session() as session:
        picked = await _batch.fetch_all_artwork(
            session, base, api_key, title,
            only_kinds=only_kinds, timeout_sec=timeout,
        )
    return {kind: (asset.url if asset else None) for kind, asset in picked.items()}


class SteamGridDBClient:
    """Instance wrapper carrying a default API key.

    Two callers use this: the Ubisoft store and the Ubisoft auth
    facade. They build a client once at construction and call
    ``search_artwork`` / ``fetch_all_kinds`` per game. Keeping it as a
    thin facade over the module-level functions avoids duplicating
    config / session-lifecycle code.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Store the API key for use in every subsequent call."""
        self.api_key = api_key

    async def search_artwork(
        self, title: str, kind: str, **_kwargs: Any,
    ) -> str | None:
        """Delegate — :func:`search_artwork`."""
        return await search_artwork(title, kind, self.api_key)

    async def fetch_all_kinds(
        self, title: str, **_kwargs: Any,
    ) -> dict[str, str | None]:
        """Delegate — :func:`fetch_all_kinds`."""
        return await fetch_all_kinds(title, self.api_key)


# Re-export the dataclass for callers building lists outside the package.
__all__ = [
    "ArtworkAsset",
    "SteamGridDBClient",
    "fetch_all_kinds",
    "search_artwork",
]
