"""services/artwork/fetcher.py — Stateless artwork fetch + save.

Pure async functions: no ``self``, each takes its inputs
explicitly so HTTP and filesystem mechanics stay testable
independent of the service orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from ...config import ConfigManager

logger = logging.getLogger(__name__)

# SGDB API documentation: https://www.steamgriddb.com/api/v2
_DEFAULT_SGDB_BASE = "https://www.steamgriddb.com/api/v2"

# Filename conventions matching Steam's expected grid/ layout.
# Extending to a new artwork type means one entry here + one in
# the ``ArtworkService`` fetch loop — no other call-site edits.
_KIND_SUFFIX = {
    "grid": "p.jpg",
    "hero": "_hero.jpg",
    "logo": "_logo.png",
    "icon": "_icon.jpg",
}

# Mapping from our internal kinds to SGDB API endpoints
_SGDB_ENDPOINTS = {
    "grid": "grids",
    "hero": "heroes",
    "logo": "logos",
    "icon": "icons",
}


async def has_artwork(grid_dir: str, app_id: int) -> bool:
    """True iff ``<app_id>p.jpg`` + ``<app_id>_hero.jpg`` both exist.

    Grid + hero are the minimum set for a game to look good in
    the Steam library — logo and icon are nice-to-haves. Uses
    async file ops so the check runs off the event loop.
    """
    def _check() -> bool:
        grid_path = os.path.join(grid_dir, f"{app_id}{_KIND_SUFFIX['grid']}")
        hero_path = os.path.join(grid_dir, f"{app_id}{_KIND_SUFFIX['hero']}")
        return os.path.isfile(grid_path) and os.path.isfile(hero_path)

    return await asyncio.to_thread(_check)


async def find_artwork_url(
    title: str,
    kind: str,
    api_key: str,
    config: ConfigManager | None = None,
) -> str | None:
    """Query SGDB for the best artwork of ``kind`` for ``title``.

    Swallows every error — an SGDB hiccup must never block a sync.
    """
    if not api_key:
        return None

    if kind not in _SGDB_ENDPOINTS:
        return None

    base_url = _DEFAULT_SGDB_BASE
    if config:
        base_url = config.get("artwork.steamgriddb_api_base", _DEFAULT_SGDB_BASE)

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Search for the game to get its SGDB ID
            search_url = f"{base_url}/search/autocomplete/{title}"
            async with session.get(search_url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("success") or not data.get("data"):
                    return None
                
                # Take the first match
                game_id = data["data"][0].get("id")
                if not game_id:
                    return None

            # 2. Get the artwork for that game
            endpoint = _SGDB_ENDPOINTS[kind]
            art_url = f"{base_url}/{endpoint}/game/{game_id}"
            
            # For grids and heroes we want dimensions that fit Steam Deck well
            params = {}
            if kind == "grid":
                params["dimensions"] = "600x900"
            elif kind == "hero":
                params["dimensions"] = "1920x1080,3840x2160"

            async with session.get(art_url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get("success") or not data.get("data"):
                    return None

                # Return the URL of the most upvoted image
                return data["data"][0].get("url")

    except Exception as e:
        logger.debug("[ArtworkFetcher] find_artwork_url failed for %s (%s): %s", title, kind, e)
        return None


async def download_and_save(
    grid_dir: str,
    app_id: int,
    kind: str,
    url: str,
    timeout: int,
) -> bool:
    """Download ``url``, save under ``grid_dir`` with Steam's naming.

    Filename = ``<app_id><_KIND_SUFFIX[kind]>``. Returns True
    only on a successful 200 + full write. HTTP errors, DNS,
    TLS, partial reads, permission, disk full — all logged
    + return False. Artwork is best-effort; next sync retries.
    """
    if kind not in _KIND_SUFFIX:
        return False

    suffix = _KIND_SUFFIX[kind]
    
    # Check if we need to convert format based on URL
    # E.g. SGDB sometimes returns PNGs for grids, Steam prefers JPG
    if url.lower().endswith('.png') and kind in ('grid', 'hero', 'icon'):
        suffix = suffix.replace('.jpg', '.png')
    elif (url.lower().endswith('.jpg') or url.lower().endswith('.jpeg')) and kind == 'logo':
        suffix = suffix.replace('.png', '.jpg')

    target_path = os.path.join(grid_dir, f"{app_id}{suffix}")
    tmp_path = target_path + ".tmp"

    try:
        # Ensure directory exists
        def _ensure_dir():
            if not os.path.isdir(grid_dir):
                os.makedirs(grid_dir, exist_ok=True)
        await asyncio.to_thread(_ensure_dir)

        # Download and write
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.debug("[ArtworkFetcher] download failed %s: HTTP %s", url, resp.status)
                    return False

                def _write_file(chunk_iter):
                    with open(tmp_path, "wb") as f:
                        for chunk in chunk_iter:
                            f.write(chunk)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_path, target_path)

                # Read all chunks in memory for small files, or use an async iterator
                # to read chunks and write them to disk.
                content = await resp.read()
                await asyncio.to_thread(_write_file, [content])
                
                return True

    except asyncio.TimeoutError:
        logger.debug("[ArtworkFetcher] download timed out: %s", url)
    except Exception as e:
        logger.debug("[ArtworkFetcher] download failed %s: %s", url, e)
    finally:
        # Cleanup tmp if left behind
        def _cleanup():
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        await asyncio.to_thread(_cleanup)

    return False
