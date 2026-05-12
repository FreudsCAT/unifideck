"""SGDB artwork fetcher — pure functions for the network layer.

OP-16c | py_modules/unifideck/services/artwork/fetcher.py

Three module-level functions :

* ``has_artwork(game_id, kind, cache_dir)`` — disk check;
* ``find_artwork_url(game_name, kind, key)`` — query SGDB and
  return the best artwork URL for the requested kind (grid, hero,
  logo, icon);
* ``download_and_save(url, target_path)`` — fetch + atomic save.

Kept module-level because they're stateless — the cache state
lives in the service, the fetcher is a pure transformer.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import ConfigManager
logger = logging.getLogger(__name__)
_KIND_SUFFIX = {
    "grid": "p.jpg",
    "hero": "_hero.jpg",
    "logo": "_logo.png",
    "icon": "_icon.jpg",
}


async def has_artwork(grid_dir: str, app_id: int) -> bool:
    """Check whether artwork."""
    from ...core.io import async_file_ops as aio

    grid_path = Path(grid_dir)
    grid_file = str(grid_path / f"{app_id}p.jpg")
    hero_file = str(grid_path / f"{app_id}_hero.jpg")
    return await aio.is_file(grid_file) and await aio.is_file(hero_file)


async def find_artwork_url(
    title: str,
    kind: str,
    api_key: str,
    config: ConfigManager | None,
) -> str | None:
    """Find artwork URL."""
    try:
        from ...steam import steamgriddb

        return await steamgriddb.search_artwork(
            title,
            kind,
            api_key,
            config=config,
        )
    except Exception as e:
        logger.debug(
            "[ArtworkService] search failed (%s/%s): %s",
            title,
            kind,
            e,
        )
        return None


async def download_and_save(
    grid_dir: str,
    app_id: int,
    kind: str,
    url: str,
    timeout: int,
) -> bool:
    """Download and save."""
    suffix = _KIND_SUFFIX.get(kind, ".jpg")
    target = str(Path(grid_dir) / f"{app_id}{suffix}")
    try:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=timeout) as resp,
        ):
            if resp.status != 200:
                return False
            data = await resp.read()
    except Exception as e:
        logger.debug(
            "[ArtworkService] download failed: %s",
            e,
        )
        return False
    from ...core.io import async_file_ops as aio

    try:
        await aio.write_bytes(target, data)
        return True
    except Exception as e:
        logger.warning(
            "[ArtworkService] save failed: %s",
            e,
        )
        return False
