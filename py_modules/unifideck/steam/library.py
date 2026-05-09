"""steam/library.py — Steam Store search + install path discovery."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)

STEAM_PATH_CANDIDATES = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.steam/steam",  # Flatpak
)

STEAM_STORE_SEARCH_URL = (
    "https://store.steampowered.com/api/storesearch"
)


@dataclass(frozen=True)
class SteamStoreResult:
    """Typed container for a Steam Store search result."""
    appid: int
    name: str
    released: str
    price_cents: int
    is_free: bool
    logo_url: str
    header_url: str


def find_steam_path(config: ConfigManager | None = None) -> str | None:
    """Return the Steam install directory, or None when not found."""
    # Logic to walk candidates and check for steamapps/
    for path in STEAM_PATH_CANDIDATES:
        full_path = os.path.expanduser(path)
        if os.path.isdir(os.path.join(full_path, "steamapps")):
            return full_path
    return None


async def search_store(query: str, timeout: float = 10.0) -> list[SteamStoreResult]:
    """Search the Steam Store via the undocumented storesearch API."""
    params = {
        "term": query,
        "l": "english",
        "cc": "us",
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(STEAM_STORE_SEARCH_URL, params=params, timeout=timeout) as response:
                if response.status != 200:
                    return []
                
                data = await response.json()
                items = data.get("items", [])
                
                results = []
                for item in items:
                    results.append(SteamStoreResult(
                        appid=item.get("id", 0),
                        name=item.get("name", ""),
                        released=item.get("released", ""),
                        price_cents=item.get("price", {}).get("final", 0),
                        is_free=item.get("price", {}).get("final", 0) == 0,
                        logo_url=item.get("tiny_image", ""),
                        header_url=f"https://cdn.akamai.steamstatic.com/steam/apps/{item.get('id')}/header.jpg"
                    ))
                return results
                
    except Exception as e:
        logger.debug("[SteamStore] Search failed for %s: %s", query, e)
        
    return []
