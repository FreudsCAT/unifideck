"""metadata/unifidb.py — UnifiDB game metadata adapter.

UnifiDB is Unifideck's community-maintained game database,
hosted on GitHub and served via jsDelivr.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

UNIFIDB_CDN_BASE = (
    "https://cdn.jsdelivr.net/gh/mubaraknumann/unifiDB@main"
)


@dataclass(frozen=True)
class UnifiDBResult:
    """Typed container for a UnifiDB lookup result."""
    unifidb_id: str
    title: str
    description: str | None
    genres: list[str]
    developer: str | None
    publisher: str | None
    release_date: str | None
    stores: dict[str, str]


async def fetch_game(store: str, store_game_id: str, title: str | None = None, timeout: float = 10.0) -> UnifiDBResult | None:
    """Look up a game in UnifiDB by store and game ID."""
    # UnifiDB index is typically organized by store/id or a master index
    # For now, we assume a master index lookup or direct mapping
    index_url = f"{UNIFIDB_CDN_BASE}/index.json"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(index_url, timeout=timeout) as response:
                if response.status != 200:
                    return None
                
                index = await response.json()
                # Find entry mapping for this store/id
                # key is usually "store:id"
                game_slug = index.get(f"{store}:{store_game_id}")
                
                if not game_slug and title:
                    # Fallback to title-based search in index
                    # This is slow and simplified; real version would use a fuzzy index
                    for key, slug in index.items():
                        if title.lower() in key.lower():
                            game_slug = slug
                            break
                
                if not game_slug:
                    return None
                
                # Fetch actual game record
                record_url = f"{UNIFIDB_CDN_BASE}/games/{game_slug}.json"
                async with session.get(record_url, timeout=timeout) as rec_resp:
                    if rec_resp.status != 200:
                        return None
                    
                    data = await rec_resp.json()
                    return _map_record(data, game_slug)
                    
    except Exception as e:
        logger.debug("[UnifiDB] Lookup failed: %s", e)
        
    return None


def _map_record(data: dict[str, Any], slug: str) -> UnifiDBResult:
    """Project a UnifiDB record into our result shape."""
    return UnifiDBResult(
        unifidb_id=slug,
        title=data.get("title", ""),
        description=data.get("description"),
        genres=data.get("genres", []),
        developer=data.get("developer"),
        publisher=data.get("publisher"),
        release_date=data.get("release_date"),
        stores=data.get("stores", {})
    )
