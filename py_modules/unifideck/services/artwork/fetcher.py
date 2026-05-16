"""SGDB artwork fetcher — pure functions for the network layer.

Pure async functions: no ``self``, each takes its inputs
explicitly so HTTP and filesystem mechanics stay testable
independent of the service orchestrator.

Steam grid/ layout accepts both JPG and PNG for grid, hero
and icon, but the on-disk extension MUST match the actual
byte content — Steam's CEF readers fail silently when a PNG
payload is saved with a ``.jpg`` name (and vice versa).
``logo`` is the strict exception: Steam requires PNG for the
overlay because it relies on alpha transparency.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)

# Steam-preferred suffix per artwork kind. Used as the
# fallback when the URL gives no format hint, and as the
# "must-have" anchor for logos (where PNG is mandatory).
_KIND_SUFFIX = {
    "grid": "p.jpg",
    "hero": "_hero.jpg",
    "logo": "_logo.png",
    "icon": "_icon.jpg",
}

# Kinds for which Steam Deck accepts both .jpg and .png.
# Logo is excluded: Steam needs PNG (alpha overlay).
_FORMAT_FLEXIBLE_KINDS = frozenset({"grid", "hero", "icon"})


def _url_extension(url: str) -> str:
    """Extract the lowercase file extension from a URL's path component.

    Pipeline:

    1. ``urlparse`` splits the URL into scheme/host/path/
       query/fragment — we only care about ``path``;
    2. ``Path(path).suffix`` returns the trailing extension
       *with* the leading dot (e.g. ``".PNG"``);
    3. ``.lower()`` normalises case so ``.PNG`` and
       ``.png`` are treated identically;
    4. ``.lstrip(".")`` drops the dot for clean
       equality checks downstream (``ext == "png"``).

    Query strings + fragments are silently ignored, which
    matters for signed CDN URLs like
    ``https://cdn.steamgriddb.com/grid.png?token=abc&v=2``
    where a naive ``url.endswith(".png")`` would return
    ``False`` and break the format-aware suffix logic.

    Args:
        url: full URL string (any scheme).

    Returns:
        Lowercase extension without the dot, e.g. ``"png"``,
        ``"jpg"``, ``"jpeg"``. Empty string when the URL
        path has no extension at all.
    """
    path = urlparse(url).path
    return Path(path).suffix.lower().lstrip(".")


def _suffix_for(kind: str, url: str) -> str:
    """Resolve the on-disk filename suffix matching an artwork download.

    Pipeline:

    1. ``kind == "logo"`` short-circuits to
       ``_logo.png`` — Steam mandates PNG for logos
       because the library renderer composites them over
       the hero with alpha blending, and a JPG would render
       as a solid white rectangle;
    2. Look up the Steam-preferred suffix from
       ``_KIND_SUFFIX``; unknown kind → ``.jpg`` (safe
       defensive default);
    3. If the kind is in ``_FORMAT_FLEXIBLE_KINDS``
       (grid/hero/icon) AND the URL extension is ``png``,
       swap the trailing ``.jpg`` for ``.png`` so the
       saved filename's extension matches the actual byte
       content. Otherwise keep the JPG default.

    The ``base.replace(".jpg", ".png")`` step is safe
    because every flexible-kind entry in ``_KIND_SUFFIX``
    ends in ``.jpg`` — there's no other ``.jpg``
    substring that could be accidentally matched.

    This function is the regression guard for the silent-
    skip bug: Steam's CEF artwork reader rejects files
    whose extension doesn't match their MIME signature,
    with no error logged anywhere — covers and heroes
    just don't render.

    Args:
        kind: artwork kind (``"grid"``, ``"hero"``,
            ``"logo"``, ``"icon"``, or arbitrary unknown).
        url: download URL — only inspected for its
            extension; never fetched here.

    Returns:
        On-disk suffix including the leading character
        (``p`` for grid, ``_hero`` for hero, etc) and
        the format-correct extension. Always a
        non-empty string.
    """
    if kind == "logo":
        return _KIND_SUFFIX["logo"]
    base = _KIND_SUFFIX.get(kind, ".jpg")
    if kind in _FORMAT_FLEXIBLE_KINDS and _url_extension(url) == "png":
        return base.replace(".jpg", ".png")
    return base


async def has_artwork(grid_dir: str, app_id: int) -> bool:
    """Predicate: is the minimum artwork set already present for this app?

    "Minimum set" means both a grid (vertical capsule) AND
    a hero (banner) — those are the two pieces of artwork
    that show in the Steam library list and on the
    game-detail page. Logo and icon are
    nice-to-haves that don't affect this gate.

    Pipeline:

    1. Lazy-import ``async_file_ops`` to avoid a heavy
       import at module load (kept lazy because this
       module is loaded by the service bootstrap which is
       latency-sensitive);
    2. Build the four candidate paths — JPG + PNG
       variants of both grid and hero — because both
       extensions are valid Steam targets and the
       format-aware ``download_and_save`` may have saved
       either flavour on the previous sync;
    3. Each artwork "exists" if at least one of its two
       extension variants is present on disk;
    4. Both grid AND hero must exist for the predicate to
       be True.

    Accepting both extensions is the anti-redownload
    guard: without it, a previous sync that saved
    ``42p.png`` (because SGDB served PNG) would not be
    found by a check looking only for ``42p.jpg``, and
    we'd hammer the SGDB API with redundant requests
    every sync cycle (~500 syncs/day on an active install).

    Args:
        grid_dir: absolute path to Steam's ``grid/``
            directory under the user's userdata folder.
        app_id: Steam application id (unsigned 32-bit).

    Returns:
        True iff both grid (jpg or png) AND hero (jpg or
        png) exist as regular files at the expected
        locations. False if either is missing or if the
        directory itself is unreadable (``aio.is_file``
        returns False on OSError).
    """
    from unifideck.core.io import async_file_ops as aio

    grid_path = Path(grid_dir)
    grid_jpg = str(grid_path / f"{app_id}p.jpg")
    grid_png = str(grid_path / f"{app_id}p.png")
    hero_jpg = str(grid_path / f"{app_id}_hero.jpg")
    hero_png = str(grid_path / f"{app_id}_hero.png")
    grid_ok = (await aio.is_file(grid_jpg) or await aio.is_file(grid_png))
    hero_ok = (await aio.is_file(hero_jpg) or await aio.is_file(hero_png))

    return grid_ok and hero_ok


async def find_artwork_url(
    title: str,
    kind: str,
    api_key: str,
    config: ConfigManager | None,
) -> str | None:
    """Resolve the best SGDB artwork URL for a given game title + kind.

    Thin delegation wrapper over ``steam.steamgriddb.search_artwork``
    (owned by OP-32a). Two responsibilities:

    1. **Lazy import** of the SGDB client to keep this
       module's import graph clean — the SGDB module pulls
       in aiohttp + config helpers, neither of which we
       want loaded just because the artwork service was
       instantiated;
    2. **Exception barrier** — SGDB outages must NEVER
       block a library sync. Any exception coming out of
       ``search_artwork`` (network, JSON parse, auth, rate
       limit, malformed response) is caught, logged at
       DEBUG (not ERROR — this is expected behaviour
       during SGDB hiccups), and translated to ``None``.

    The caller treats ``None`` as "no artwork available
    right now, try again next sync" — there's no retry
    here because the service-level sync loop already
    drives the retry cadence.

    Args:
        title: game title to search for (caller is
            responsible for any normalisation —
            we pass it through verbatim).
        kind: artwork kind to fetch (``"grid"``,
            ``"hero"``, ``"logo"``, ``"icon"``); SGDB
            endpoints are resolved inside ``search_artwork``.
        api_key: SGDB API key (Bearer token). Empty
            string is acceptable — ``search_artwork``
            handles the "no key" case by returning None.
        config: optional ``ConfigManager`` for overriding
            the SGDB base URL (used in tests + when
            self-hosting an SGDB mirror).

    Returns:
        Absolute HTTPS URL of the highest-ranked artwork
        of the requested kind, or ``None`` on any failure
        mode (no key, no match, network error, malformed
        response, etc).
    """
    try:
        from unifideck.steam import steamgriddb
        return await steamgriddb.search_artwork(title, kind, api_key, config=config)
    except Exception as e:
        logger.debug("[ArtworkService] search failed (%s/%s): %s", title, kind, e)
        return None


async def _fetch_url_bytes(url: str, timeout: int) -> bytes | None:
    """Download ``url`` and return its body bytes, or None on failure.

    Pure network slice extracted from ``download_and_save``:

    * Fresh ``aiohttp.ClientSession`` per call — artwork
      downloads are infrequent enough that pooling overhead
      isn't worth the lifecycle complexity. The combined
      ``async with`` closes both session and response even
      on exception.
    * Non-200 response → ``None`` with no log (404 from
      SGDB CDN is routine during reindex events).
    * Any HTTP-side exception (timeout, DNS, TLS, partial
      read, reset) → ``None`` at DEBUG level — transient
      and retryable next sync cycle.

    Caller responsibility: distinguishing a fetch failure
    from an empty body. Both manifest as ``None`` here.
    """
    try:
        import aiohttp
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=client_timeout) as resp,
        ):
            if resp.status != 200:
                return None
            return await resp.read()
    except Exception as e:
        logger.debug("[ArtworkService] download failed: %s", e)
        return None


async def download_and_save(
    grid_dir: str, app_id: int, kind: str, url: str, timeout: int,
) -> bool:
    """Download an artwork URL and save it to Steam's grid directory.

    Three-stage pipeline:

    1. **Suffix resolution** via ``_suffix_for(kind, url)``
       so the saved filename's extension matches the
       actual byte content — Steam's CEF artwork reader
       rejects files with mismatched extension + MIME.
    2. **HTTP fetch** via ``_fetch_url_bytes`` (extracted
       helper handling status check + HTTP exception
       barrier at DEBUG level).
    3. **Atomic write** via ``async_file_ops.write_bytes``
       which handles the tmp-file + ``os.replace`` +
       fsync dance. Filesystem exceptions logged at WARN
       — these typically indicate a real config problem
       (permission, disk full, RO mount) the user should
       see in the Decky log.

    Artwork is intentionally best-effort: any failure
    returns False without raising. The next sync cycle
    will retry the same URL.

    Args:
        grid_dir: absolute path to Steam's ``grid/``
            directory. Must already exist — directory
            bootstrap is the caller's responsibility.
        app_id: Steam application id (unsigned 32-bit).
        kind: artwork kind for suffix resolution.
        url: HTTPS URL to download from (typically an
            SGDB CDN URL with a signed query string).
        timeout: total HTTP timeout in seconds.

    Returns:
        True iff fetch returned 200 AND bytes persisted to
        disk. False on any failure mode. Never raises.
    """
    suffix = _suffix_for(kind, url)
    target = str(Path(grid_dir) / f"{app_id}{suffix}")
    data = await _fetch_url_bytes(url, timeout)
    if data is None:
        return False
    from unifideck.core.io import async_file_ops as aio
    try:
        await aio.write_bytes(target, data)
        return True
    except Exception as e:
        logger.warning("[ArtworkService] save failed: %s", e)
        return False
