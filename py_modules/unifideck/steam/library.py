"""steam/library.py — Steam install discovery and Store search.

Three pieces of functionality used across the plugin live here:

* :func:`find_steam_path` — locate the Steam install root.
* :func:`get_steam_library_names` — enumerate every game installed
  on the native Steam account (via the ACF manifests under each
  configured library folder).
* :func:`search_store` — query the Steam Store search API for an
  ``appid`` given a free-form title.

Compatibility ratings (ProtonDB / Steam Deck Verified) used to live
here under a ``CompatLibrary`` class; that code was moved to
``unifideck.compatibility.library`` and is no longer re-exported
from this module. The two responsibilities were always orthogonal
(local-disk scan vs. remote rating lookup) and the duplication
between the modules had drifted, so the split is final.

All functions are read-only with respect to the Steam install: no
file is created or modified. ``search_store`` is the only function
that touches the network; failures are swallowed and logged at
``DEBUG`` so callers can use ``or None`` semantics.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

import aiohttp

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from collections.abc import Iterable

    from unifideck.config import ConfigManager


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #


# Searched in order. The first candidate whose ``steamapps`` subdir
# exists wins. ``~/.steam/steam`` is normally a symlink to one of
# the others; we resolve it before returning.
_STEAM_PATH_CANDIDATES: tuple[str, ...] = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
)

_STEAM_STORE_SEARCH_URL = (
    "https://store.steampowered.com/api/storesearch/"
    "?term={term}&l=english&cc=us"
)

# Conservative timeout for the Store API call. The Store endpoint
# is normally fast (<500 ms), so anything beyond this means the
# network is wedged and the caller would rather get ``None``.
_STEAM_STORE_SEARCH_TIMEOUT_S = 8.0

# Cheap regex parsers for the small subset of Valve's KeyValues
# format that we actually need. The files are well-formed enough
# that a real VDF parser would be overkill; matches across the
# whole stdlib regex engine in microseconds.
_ACF_NAME_PATTERN = re.compile(r'"name"\s+"([^"]*)"')
_LIBFOLDER_PATH_PATTERN = re.compile(r'"path"\s+"([^"]*)"')


# --------------------------------------------------------------------------- #
# find_steam_path
# --------------------------------------------------------------------------- #


def find_steam_path(config: ConfigManager | None = None) -> Path | None:
    """Locate the Steam install root.

    Resolution order:

    1. The user-overridden path under ``steam.install_path`` in
       config, if it points to an existing directory containing a
       ``steamapps`` subdirectory.
    2. The first entry in :data:`_STEAM_PATH_CANDIDATES` whose
       ``steamapps`` subdirectory exists.

    Args:
        config: Optional ``ConfigManager`` for the user override.

    Returns:
        Absolute ``Path`` to the Steam root (the directory that
        contains ``steamapps/``), or ``None`` if no Steam install
        could be located.
    """
    # 1. User override first.
    override_raw = get_cfg(config, "steam.install_path", "")
    if isinstance(override_raw, str) and override_raw:
        override = Path(override_raw).expanduser()
        if (override / "steamapps").is_dir():
            return override
        logger.debug(
            "[steam.library] user override %r does not contain steamapps/,"
            " falling back to defaults",
            override_raw,
        )

    # 2. Standard candidates.
    for candidate in _STEAM_PATH_CANDIDATES:
        path = Path(candidate).expanduser()
        # ``~/.steam/steam`` is normally a symlink — resolve it so the
        # returned path is canonical and stable across reboots.
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path
        if (resolved / "steamapps").is_dir():
            return resolved

    logger.debug("[steam.library] no Steam install located")
    return None


# --------------------------------------------------------------------------- #
# get_steam_library_names
# --------------------------------------------------------------------------- #


def _list_library_roots(steam_path: Path) -> list[Path]:
    """Return every library folder Steam considers active.

    Always includes the main Steam install. Walks
    ``steamapps/libraryfolders.vdf`` to discover additional roots
    (typically external drives or SD cards on a Deck). Missing or
    unreadable files are silently skipped: the caller treats the
    return value as an exhaustive best-effort list, not as a
    contract.
    """
    roots: list[Path] = [steam_path]

    libfolders_vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if not libfolders_vdf.is_file():
        return roots

    try:
        text = libfolders_vdf.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug(
            "[steam.library] cannot read %s: %s", libfolders_vdf, e,
        )
        return roots

    for match in _LIBFOLDER_PATH_PATTERN.finditer(text):
        raw_path = match.group(1)
        # The VDF file double-escapes backslashes on Windows-style
        # installs (Proton prefixes don't appear here, but be safe).
        unescaped = raw_path.replace("\\\\", "/").replace("\\", "/")
        extra = Path(unescaped).expanduser()
        if extra.is_dir() and extra != steam_path:
            roots.append(extra)

    return roots


def _extract_name_from_manifest(acf_path: Path) -> str | None:
    """Parse a single ``appmanifest_*.acf`` and return its ``"name"`` field.

    Returns ``None`` for unreadable / unparseable files so the caller
    can skip them without surfacing the failure.
    """
    try:
        text = acf_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _ACF_NAME_PATTERN.search(text)
    if match is None:
        return None
    name = match.group(1).strip()
    return name or None


def get_steam_library_names(
    config: ConfigManager | None = None,
) -> Iterable[str]:
    """Yield the display name of every locally-installed Steam app.

    Walks every library folder Steam knows about and parses the
    ``appmanifest_<appid>.acf`` files. Reads only what's already
    on disk; never hits the network and never modifies anything.

    Args:
        config: Optional ``ConfigManager`` forwarded to
            ``find_steam_path`` for the install-path override.

    Returns:
        A ``list[str]`` of game names (a concrete sequence, not a
        generator, so the caller can iterate it multiple times).
        Empty list if Steam isn't installed or every library is
        unreadable.
    """
    steam_path = find_steam_path(config)
    if steam_path is None:
        return []

    names: list[str] = []
    for root in _list_library_roots(steam_path):
        steamapps = root / "steamapps"
        if not steamapps.is_dir():
            continue
        try:
            manifests = list(steamapps.glob("appmanifest_*.acf"))
        except OSError as e:
            logger.debug(
                "[steam.library] cannot list %s: %s", steamapps, e,
            )
            continue
        for manifest in manifests:
            name = _extract_name_from_manifest(manifest)
            if name:
                names.append(name)

    return names


# --------------------------------------------------------------------------- #
# search_store
# --------------------------------------------------------------------------- #


async def _fetch_search_payload(url: str) -> dict[str, Any] | None:
    """HTTP GET ``url`` and parse JSON, returning ``None`` on any failure.

    Failure modes collapsed to ``None`` :
        * HTTP transport error or timeout (``ClientError``,
          ``TimeoutError``).
        * Non-200 status.
        * Response body that doesn't decode as JSON.

    Logs every miss at DEBUG so a noisy outage doesn't flood
    the WARN channel — caller's contract is "soft miss".
    """
    client_timeout = aiohttp.ClientTimeout(total=_STEAM_STORE_SEARCH_TIMEOUT_S)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session, \
                session.get(url) as resp:
            if resp.status != 200:
                logger.debug(
                    "[steam.library] search_store HTTP %s for %s",
                    resp.status, url,
                )
                return None
            try:
                payload = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError) as err:
                logger.debug(
                    "[steam.library] search_store JSON decode failed: %s", err,
                )
                return None
    except (aiohttp.ClientError, TimeoutError) as err:
        logger.debug(
            "[steam.library] search_store HTTP failed for %s: %s",
            url, err,
        )
        return None
    return payload if isinstance(payload, dict) else None


def _extract_first_item(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the first usable item from a Steam Store search payload.

    Defensive against three shape variations seen in the wild :
        * No ``items`` key (legacy "no result" shape).
        * ``items`` is not a list (API change).
        * ``items`` is an empty list (zero matches).
        * First item is not a dict (rare but observed).
    """
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    return first if isinstance(first, dict) else None


async def search_store(
    title: str,
    config: ConfigManager | None = None,
) -> dict[str, Any] | None:
    """Query Steam's Store search API for one title.

    The endpoint at
    ``https://store.steampowered.com/api/storesearch/?term=<title>``
    returns a JSON envelope with the form
    ``{"total": N, "items": [{"id": <appid>, "name": "...", ...}]}``.
    We only ever look at the first item.

    Failures (network error, non-200 response, malformed JSON,
    empty result set) are swallowed and logged at ``DEBUG``; the
    caller receives ``None`` and is expected to treat the lookup
    as a soft miss.

    Args:
        title: Free-form game name.
        config: Reserved for future use (caching / proxy / user
            agent). Currently unused but kept in the signature for
            API stability.

    Returns:
        A dict with at least the keys ``"app_id"`` (``int``) and
        ``"name"`` (``str``), plus the raw API entry under
        ``"raw"``; or ``None`` if no match was found.
    """
    if not title or not title.strip():
        return None

    url = _STEAM_STORE_SEARCH_URL.format(term=quote_plus(title.strip()))
    payload = await _fetch_search_payload(url)
    if payload is None:
        return None

    first = _extract_first_item(payload)
    if first is None:
        return None

    try:
        # ``first.get("id")`` returns Any | None; ``int(None)`` raises
        # TypeError so the except below catches it, but mypy strict
        # needs the explicit None-guard via the str() cast.
        raw_id = first.get("id")
        if raw_id is None:
            return None
        app_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    return {
        "app_id": app_id,
        "name": first.get("name", title),
        "raw": first,
    }
