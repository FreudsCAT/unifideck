"""services/artwork/fetcher.py — Stateless artwork fetch + save.

Pure async functions: no ``self``, each takes its inputs
explicitly so HTTP and filesystem mechanics stay testable
independent of the service orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import aiohttp

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

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


async def _sgdb_lookup_game_id(
    session: aiohttp.ClientSession,
    base_url: str,
    title: str,
) -> int | None:
    """Resolve a game title to its SGDB numeric ID via /search/autocomplete.

    Returns the most-confident match's ID, or ``None`` if SGDB
    answered non-200, returned ``success=False``, returned an
    empty ``data`` list, or the first entry has no ``id``.
    The title is URL-quoted so titles with spaces / colons /
    accents survive the path segment (e.g. *Hollow Knight: Silksong*).
    """
    search_url = f"{base_url}/search/autocomplete/{quote(title)}"
    async with session.get(search_url) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
    if not data.get("success") or not data.get("data"):
        return None
    return data["data"][0].get("id")


def _sgdb_kind_params(kind: str) -> dict[str, str]:
    """Build the dimensions filter for the SGDB artwork endpoint.

    Grid art uses the Steam vertical capsule (600x900); hero art
    uses Steam Deck-friendly widescreens (1080p + 4K so we have a
    crisp option to fall back on). Other kinds (icon, logo) take
    no dimension hint.
    """
    if kind == "grid":
        return {"dimensions": "600x900"}
    if kind == "hero":
        return {"dimensions": "1920x1080,3840x2160"}
    return {}


async def _sgdb_pick_artwork_url(
    session: aiohttp.ClientSession,
    base_url: str,
    game_id: int,
    kind: str,
) -> str | None:
    """Query SGDB's per-kind endpoint and pick the top artwork URL.

    Returns ``None`` on any error (non-200, malformed payload,
    empty result). SGDB orders results by upvote count, so the
    first entry is the community pick.
    """
    endpoint = _SGDB_ENDPOINTS[kind]
    art_url = f"{base_url}/{endpoint}/game/{game_id}"
    params = _sgdb_kind_params(kind)
    async with session.get(art_url, params=params) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
    if not data.get("success") or not data.get("data"):
        return None
    return data["data"][0].get("url")


async def find_artwork_url(
    title: str,
    kind: str,
    api_key: str,
    config: ConfigManager | None = None,
) -> str | None:
    """Query SGDB for the best artwork of ``kind`` for ``title``.

    Two-step: (1) resolve title → SGDB game id, (2) pick top
    artwork URL for the requested kind. Swallows every error —
    an SGDB hiccup must never block a sync.
    """
    if not api_key or kind not in _SGDB_ENDPOINTS:
        return None

    base_url = _DEFAULT_SGDB_BASE
    if config:
        base_url = config.get("artwork.steamgriddb_api_base", _DEFAULT_SGDB_BASE)

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            game_id = await _sgdb_lookup_game_id(session, base_url, title)
            if not game_id:
                return None
            return await _sgdb_pick_artwork_url(session, base_url, game_id, kind)
    except Exception as e:
        logger.debug(
            "[ArtworkFetcher] find_artwork_url failed for %s (%s): %s",
            title, kind, e,
        )
        return None


def _adjust_artwork_suffix(suffix: str, url: str, kind: str) -> str:
    """Pick the right file extension based on the source URL.

    SGDB sometimes serves PNG for kinds we'd default to JPG (and
    vice-versa for logos). We match the URL's extension so the
    on-disk format matches the bytes we're about to write, which
    Steam's renderer expects.
    """
    lower = url.lower()
    if lower.endswith(".png") and kind in ("grid", "hero", "icon"):
        return suffix.replace(".jpg", ".png")
    if lower.endswith((".jpg", ".jpeg")) and kind == "logo":
        return suffix.replace(".png", ".jpg")
    return suffix


async def _download_artwork_bytes(
    url: str,
    timeout: int,
) -> bytes | None:
    """Fetch ``url`` and return its bytes, or ``None`` on any failure.

    Encapsulates the HTTP plumbing so :func:`download_and_save`
    can focus on file placement. Non-200 responses log and return
    None — they're not exceptional, SGDB CDN occasionally 403s.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.debug(
                    "[ArtworkFetcher] download failed %s: HTTP %s",
                    url, resp.status,
                )
                return None
            return await resp.read()


def _ensure_grid_dir(grid_dir: str) -> None:
    """Create the grid dir lazily so first-time writers don't fail.

    Module-scope (not nested) so it doesn't count against
    :func:`download_and_save`'s mccabe complexity. ``exist_ok``
    protects against the race where two artwork tasks both miss
    the dir and try to create it simultaneously.
    """
    if not Path(grid_dir).is_dir():
        Path(grid_dir).mkdir(parents=True, exist_ok=True)


def _atomic_write_artwork(tmp_path: str, target_path: str, content: bytes) -> None:
    """Atomic write: tmp file, fsync, rename. Avoids torn artwork.

    Steam's renderer reads the file while we're writing it on
    every grid refresh, so a direct write would race and show a
    half-rendered image. The fsync + os.replace combo makes the
    swap atomic at the OS level on every filesystem we target.
    """
    with open(tmp_path, "wb") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)


def _cleanup_artwork_tmp(tmp_path: str) -> None:
    """Best-effort tmp removal — runs even on exceptional exit.

    Called from ``finally:`` so a torn download doesn't leave
    ``.tmp`` litter accumulating in the grid dir. Failures here
    are logged at debug level only; the user's next sync will
    overwrite or skip them.
    """
    if not Path(tmp_path).exists():
        return
    try:
        Path(tmp_path).unlink()
    except OSError as e:
        logger.debug(
            "[ArtworkFetcher] tmp cleanup failed %s: %s",
            tmp_path, e,
        )


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

    suffix = _adjust_artwork_suffix(_KIND_SUFFIX[kind], url, kind)
    target_path = os.path.join(grid_dir, f"{app_id}{suffix}")
    tmp_path = target_path + ".tmp"

    try:
        await asyncio.to_thread(_ensure_grid_dir, grid_dir)
        content = await _download_artwork_bytes(url, timeout)
        if content is None:
            return False
        await asyncio.to_thread(_atomic_write_artwork, tmp_path, target_path, content)
        return True
    except asyncio.TimeoutError:
        logger.debug("[ArtworkFetcher] download timed out: %s", url)
        return False
    except Exception as e:
        logger.debug("[ArtworkFetcher] download failed %s: %s", url, e)
        return False
    finally:
        await asyncio.to_thread(_cleanup_artwork_tmp, tmp_path)
