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
    """Return whether the canonical grid + hero artwork exist on disk.

    The check is intentionally conservative: it verifies the
    presence of both the ``<id>p.jpg`` (grid) and ``<id>_hero.jpg``
    (hero) files, which are the two artwork kinds that matter most
    for the Steam library UI. Logo and icon are nice-to-have and not
    required for the check to pass.

    Args:
        grid_dir: absolute path to Steam's ``grid`` directory.
        app_id: Steam app id (the file naming key).

    Returns:
        ``True`` iff both files are present.
    """
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
    """Query SteamGridDB for the best-matching artwork URL.

    Delegates to ``steam.steamgriddb.search_artwork`` which does the
    HTTP call and ranks results. Network / parsing / rate-limit
    failures are swallowed and surfaced as ``None`` — the caller
    (``ArtworkService.fetch_artwork``) treats ``None`` as a "no
    artwork available" signal rather than a hard error.

    Args:
        title: game title used as the search query.
        kind: one of ``"grid"`` / ``"hero"`` / ``"logo"`` / ``"icon"``.
        api_key: SGDB API key (may be empty for unauthenticated
            usage which is rate-limited more aggressively).
        config: optional config manager (forwarded to the
            steamgriddb module for its own tunables like timeout).

    Returns:
        Best-matching artwork URL string, or ``None`` if no match
        was found or the call failed.
    """
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
    """Download the artwork from ``url`` and write it under ``grid_dir``.

    Target filename follows Steam's grid-naming convention encoded
    in ``_KIND_SUFFIX`` (e.g. ``<id>p.jpg`` for grid,
    ``<id>_hero.jpg`` for hero). The write is atomic at the
    filesystem level (delegated to ``async_file_ops.write_bytes``).

    Non-200 HTTP responses, network failures and filesystem errors
    are all caught and logged at DEBUG (network) or WARN (write
    error) and surface as ``False`` — the caller treats ``False``
    as "skip this artwork" rather than as a hard failure.

    Args:
        grid_dir: absolute path to Steam's ``grid`` directory.
        app_id: Steam app id.
        kind: one of ``"grid"`` / ``"hero"`` / ``"logo"`` / ``"icon"``.
        url: full URL to download.
        timeout: HTTP timeout in seconds.

    Returns:
        ``True`` on successful download + save, ``False`` on any
        kind of failure.
    """
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
