"""SteamGridDB API client.

Searches and downloads grid / hero / logo / icon artwork from
the SteamGridDB community database to populate Steam's grid
directory for non-Steam shortcuts.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
SGDB_API_BASE = "https://www.steamgriddb.com/api/v2"
ARTWORK_KINDS = {
    "grid": ("grids", ["600x900"]),
    "hero": ("heroes", ["1920x620", "3840x1240"]),
    "logo": ("logos", None),
    "icon": ("icons", None),
}
@dataclass
class ArtworkAsset:
    """Artwork asset."""

    url: str
    width: int
    height: int
    style: str
    mime: str
    game_id: int
    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return {
            "url": self.url,
            "width": self.width,
            "height": self.height,
            "style": self.style,
            "mime": self.mime,
            "game_id": self.game_id,
        }
async def search_artwork(
    title: str,
    kind: str,
    api_key: str | None = None,
    config: ConfigManager | None = None,
) -> str | None:
    """Search artwork."""
    if kind not in ARTWORK_KINDS:
        raise ValueError(f"unknown artwork kind: {kind}")
    if not api_key:
        return None
    base = get_cfg(
        config, "artwork.steamgriddb_api_base",
        "https://www.steamgriddb.com/api/v2",
    )
    timeout = get_cfg(config, "artwork.download_timeout_seconds", 30)
    game = await _search_game(title, api_key, base, timeout)
    if game is None:
        return None
    endpoint, _dimensions = ARTWORK_KINDS[kind]
    assets = await _fetch_assets(
        game["id"], endpoint, api_key, base, timeout,
    )
    best = _pick_best_asset(assets)
    return best.url if best else None

async def fetch_all_kinds(
    title: str,
    api_key: str | None,
    config: ConfigManager | None = None,
) -> dict[str, str | None]:
    """Fetch all kinds."""
    if not api_key:
        return dict.fromkeys(ARTWORK_KINDS)
    base = get_cfg(
        config, "artwork.steamgriddb_api_base",
        "https://www.steamgriddb.com/api/v2",
    )
    timeout = get_cfg(config, "artwork.download_timeout_seconds", 30)
    game = await _search_game(title, api_key, base, timeout)
    if game is None:
        return dict.fromkeys(ARTWORK_KINDS)
    game_id = game["id"]
    results: dict[str, str | None] = {}
    for kind, (endpoint, _dims) in ARTWORK_KINDS.items():
        assets = await _fetch_assets(
            game_id, endpoint, api_key, base, timeout,
        )
        best = _pick_best_asset(assets)
        results[kind] = best.url if best else None
    return results
def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Cfg."""
    return get_cfg(config, key, default)
async def _search_game(
    title: str, api_key: str, base: str, timeout: int,
) -> dict[str, Any] | None:
    """Search game."""
    import aiohttp
    url = f"{base}/search/autocomplete/{title}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, headers=headers, timeout=timeout,
            ) as resp,
        ):
            if resp.status != 200:
                logger.debug(
                    "[sgdb] search(%s) → HTTP %d",
                    title, resp.status,
                )
                return None
            payload = await resp.json()
    except (aiohttp.ClientError, OSError, ValueError, asyncio.TimeoutError) as e:
        logger.debug(
            "[sgdb] search(%s) failed: %s", title, e,
        )
        return None
    if not payload.get("success"):
        return None
    data = payload.get("data") or []
    return data[0] if data else None

async def _fetch_assets(
    game_id: int, endpoint: str, api_key: str,
    base: str, timeout: int,
) -> list[ArtworkAsset]:
    """Fetch assets."""
    import aiohttp
    url = f"{base}/{endpoint}/game/{game_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, headers=headers, timeout=timeout,
            ) as resp,
        ):
            if resp.status != 200:
                return []
            payload = await resp.json()
    except (aiohttp.ClientError, OSError, ValueError, asyncio.TimeoutError) as e:
        logger.debug(
            "[sgdb] fetch(%d, %s) failed: %s",
            game_id, endpoint, e,
        )
        return []
    if not payload.get("success"):
        return []
    results: list[ArtworkAsset] = []
    for item in payload.get("data", []):
        try:
            results.append(ArtworkAsset(
                url=item["url"],
                width=item.get("width", 0),
                height=item.get("height", 0),
                style=item.get("style", ""),
                mime=item.get("mime", "image/png"),
                game_id=game_id,
            ))
        except KeyError:
            continue
    return results
def _pick_best_asset(
    assets: list[ArtworkAsset],
) -> ArtworkAsset | None:
    """Pick best asset."""
    if not assets:
        return None
    def rank(asset: ArtworkAsset) -> tuple:
        """Rank."""
        style_rank = 1 if asset.style == "alternate" else 0
        res = asset.width * asset.height
        return (style_rank, res)
    return max(assets, key=rank)
class SteamGridDBClient:
    """Steam grid dbclient."""

    def __init__(self, api_key=None):
        """Initialize the instance."""
        self.api_key = api_key
    async def search_artwork(self, title, kind, **kwargs):
        """Search artwork."""
        return await search_artwork(title, kind, self.api_key)
    async def fetch_all_kinds(self, title, **kwargs):
        """Fetch all kinds."""
        return await fetch_all_kinds(title, self.api_key)
