"""Parallel kind fetch — one search + N parallel asset fetches per game.

The current branch had ``fetch_all_kinds`` call ``find_artwork_url``
sequentially per kind, doing **5 separate** SGDB searches (one per
kind) + 5 separate asset fetches per game. That's 10 round-trips per
game; on a 1000-game library that's 10000 round-trips and rate-limit
risk on the free tier.

This module does it once: one search to resolve the game-id, then
``asyncio.gather`` across the requested kinds for parallel asset
fetches. 1 search + N parallel asset calls = 6 round-trips worst case
(but parallel) for the full 5 kinds. Faster *and* gentler on the API.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .assets import ArtworkAsset, fetch_with_fallback
from .constants import ARTWORK_KINDS
from .ranking import pick_best
from .search import search_game_id

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


async def _resolve_kind(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    game_id: int,
    kind: str,
    timeout_sec: int,
) -> ArtworkAsset | None:
    """One kind = one fetch_with_fallback + pick_best.

    Wrapped so the parallel ``gather`` in :func:`fetch_all_artwork`
    can request multiple kinds without inlining the fetch+pick steps.
    """
    assets = await fetch_with_fallback(
        session, base, api_key, game_id, kind, timeout_sec=timeout_sec,
    )
    return pick_best(assets)


async def fetch_all_artwork(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    title: str,
    *,
    only_kinds: frozenset[str] | None = None,
    timeout_sec: int,
) -> dict[str, ArtworkAsset | None]:
    """Resolve a title to its best artwork URL per kind.

    Args:
        only_kinds: when set, fetch only these kinds (e.g.
            ``frozenset({"grid", "hero"})`` after a per-store API
            already provided logo + icon). ``None`` means all 5.

    Returns ``{kind: ArtworkAsset | None}`` for every requested kind.
    ``None`` means "search succeeded but no asset" or "search failed";
    caller can tell from logs but the return shape stays uniform.

    If the initial title→game_id search fails, every kind maps to
    ``None`` (no point fetching assets without a game-id). This is the
    single-search optimisation the old per-kind ``find_artwork_url``
    sacrificed.
    """
    kinds = tuple(only_kinds) if only_kinds else ARTWORK_KINDS
    empty: dict[str, ArtworkAsset | None] = dict.fromkeys(kinds)

    game_id = await search_game_id(
        session, base, api_key, title, timeout_sec=timeout_sec,
    )
    if game_id is None:
        return empty

    coros = [
        _resolve_kind(session, base, api_key, game_id, k, timeout_sec)
        for k in kinds
    ]
    picked = await asyncio.gather(*coros, return_exceptions=True)
    out: dict[str, ArtworkAsset | None] = {}
    for kind, result in zip(kinds, picked, strict=True):
        if isinstance(result, BaseException):
            logger.debug(
                "[sgdb.batch] kind=%s failed: %s: %s",
                kind, type(result).__name__, result,
            )
            out[kind] = None
        else:
            out[kind] = result
    return out
