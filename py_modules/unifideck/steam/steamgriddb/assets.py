"""SGDB asset-fetch HTTP layer.

Two responsibilities:

* :class:`ArtworkAsset` — the on-the-wire dataclass returned by the
  SGDB ``/grids|heroes|logos|icons/game/{id}`` endpoints. Mirrors the
  fields we actually use; ignores the rest of the response shape.

* :func:`fetch_assets` + :func:`fetch_with_fallback` — strict-then-relaxed
  fetch that always sends ``nsfw=false&humor=false`` plus the
  dimension/style filters from :mod:`constants`. The relaxed fallback
  recovers art for games that have no exact-dimension match (common for
  niche / older titles).

Why the relaxed fallback matters
================================
The strict fetch limits results to Steam's preferred dimensions
(``600x900`` portrait, ``920x430`` landscape, ``1920x620`` hero). For
many games that returns an empty list — the only SGDB submissions
might be at off-spec sizes. Without the fallback, those games show no
art on the tile. Staging shipped a two-level fetch for exactly this
reason; for-pr-0.7 dropped it during the refactor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .constants import KIND_DEFAULTS, KIND_ENDPOINT, KIND_RELAXED

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class ArtworkAsset:
    """SGDB asset wire-shape — fields we read from the API.

    ``score`` and ``is_locked`` are 0 / False for most assets because
    SGDB API v2 zeroes them out, but they're carried so the ranking
    layer can future-proof against the day they're populated.
    """

    url: str
    width: int
    height: int
    style: str
    mime: str
    game_id: int
    score: int = 0
    is_locked: bool = False
    nsfw: bool = False
    humor: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Round-trip helper for callers that want plain dicts."""
        return {
            "url": self.url,
            "width": self.width,
            "height": self.height,
            "style": self.style,
            "mime": self.mime,
            "game_id": self.game_id,
            "score": self.score,
            "is_locked": self.is_locked,
            "nsfw": self.nsfw,
            "humor": self.humor,
        }


def _build_query_params(
    dimensions: str | None, styles: str | None,
) -> dict[str, str]:
    """Compose the SGDB query-string params for a single fetch.

    Always sends ``nsfw=false`` and ``humor=false`` — Steam's grid
    UI isn't the place for either, and SGDB's free tier mixes them
    in unless filtered.
    """
    params: dict[str, str] = {"nsfw": "false", "humor": "false"}
    if dimensions:
        params["dimensions"] = dimensions
    if styles:
        params["styles"] = styles
    return params


def _asset_from_payload(item: dict[str, Any], game_id: int) -> ArtworkAsset | None:
    """Parse a single SGDB item dict into an :class:`ArtworkAsset`.

    Returns ``None`` if the item lacks a URL — that's the only field
    we genuinely require; everything else can default.
    """
    url = item.get("url")
    if not url:
        return None
    return ArtworkAsset(
        url=url,
        width=int(item.get("width", 0)),
        height=int(item.get("height", 0)),
        style=str(item.get("style", "")),
        mime=str(item.get("mime", "image/png")),
        game_id=game_id,
        score=int(item.get("score", 0) or 0),
        is_locked=bool(item.get("lock", False)),
        nsfw=bool(item.get("nsfw", False)),
        humor=bool(item.get("humor", False)),
    )


async def fetch_assets(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    game_id: int,
    kind: str,
    *,
    dimensions: str | None,
    styles: str | None,
    timeout_sec: int,
) -> list[ArtworkAsset]:
    """Single HTTP call to ``/<endpoint>/game/<id>`` with filter params.

    Returns the parsed asset list on success; empty list on any HTTP
    error / timeout / JSON parse failure. Never raises — caller can
    chain into :func:`fetch_with_fallback` without exception handling.
    """
    import aiohttp

    endpoint = KIND_ENDPOINT.get(kind)
    if endpoint is None:
        return []
    url = f"{base}/{endpoint}/game/{game_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = _build_query_params(dimensions, styles)
    try:
        async with session.get(
            url,
            headers=headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            if resp.status != 200:
                # Surface auth/rate-limit/5xx (systemic); keep 404 quiet.
                level = (
                    logging.WARNING
                    if resp.status in (401, 403, 429) or resp.status >= 500
                    else logging.DEBUG
                )
                logger.log(
                    level, "[sgdb.assets] %s/game/%d → HTTP %d",
                    endpoint, game_id, resp.status,
                )
                return []
            payload = await resp.json()
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as e:
        # Promoted from DEBUG: a network/TLS/DNS failure here drops the
        # kind to empty; if it fires for every kind the whole library
        # loses its SGDB art silently (the bug this guards against).
        logger.warning(
            "[sgdb.assets] %s/game/%d failed: %s: %s",
            endpoint, game_id, type(e).__name__, e,
        )
        return []
    if not payload.get("success"):
        return []
    out: list[ArtworkAsset] = []
    for item in payload.get("data", []):
        asset = _asset_from_payload(item, game_id)
        if asset is not None:
            out.append(asset)
    return out


async def fetch_with_fallback(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    game_id: int,
    kind: str,
    *,
    timeout_sec: int,
) -> list[ArtworkAsset]:
    """Strict fetch, then relaxed retry if empty.

    Pulls ``(dimensions, styles)`` from :data:`KIND_DEFAULTS` for the
    first attempt; if that comes back empty, falls back to
    :data:`KIND_RELAXED` (broader dimensions, no style restriction).
    This is the only fetch entry-point callers need — the strict /
    relaxed split is an implementation detail.
    """
    dims, styles = KIND_DEFAULTS.get(kind, (None, None))
    assets = await fetch_assets(
        session, base, api_key, game_id, kind,
        dimensions=dims, styles=styles, timeout_sec=timeout_sec,
    )
    if assets:
        return assets
    relax_dims, relax_styles = KIND_RELAXED.get(kind, (None, None))
    # If the relaxed params are identical to the strict ones, skip the
    # retry — same query, same result.
    if relax_dims == dims and relax_styles == styles:
        return assets
    logger.debug(
        "[sgdb.assets] relaxed fallback for kind=%s game_id=%d",
        kind, game_id,
    )
    return await fetch_assets(
        session, base, api_key, game_id, kind,
        dimensions=relax_dims, styles=relax_styles, timeout_sec=timeout_sec,
    )
