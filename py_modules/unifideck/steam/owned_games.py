"""Owned Steam games detection.

Walks the user's Steam config to enumerate apps already owned
on the native Steam account so we can avoid re-importing them
as non-Steam shortcuts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.metadata.unifidb import normalize_title_for_matching

from .library import find_steam_path

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
_ACF_NAME_PATTERN = re.compile(r'"name"\s+"([^"]*)"')
_LIBFOLDER_PATH_PATTERN = re.compile(r'"path"\s+"([^"]*)"')
_Fingerprint = tuple[str, float | None, tuple[tuple[str, float | None], ...]]
_cache: tuple[_Fingerprint, frozenset[str]] | None = None
# Owned-but-not-installed titles can't be read from appmanifests, so the
# frontend (which can enumerate the full Steam library via collectionStore)
# pushes them here for the backend filter to read.
_FRONTEND_CACHE_PATH = Path(
    "~/.local/share/unifideck/steam_owned_titles.json",
).expanduser()


def save_frontend_owned_titles(raw_titles: list[str]) -> int:
    """Persist the frontend-supplied Steam-owned game titles (normalised).

    Stores the shared-normaliser form so the filter reads them directly.
    Atomic write; returns the number of titles stored.
    """
    normalized = sorted({
        n for t in raw_titles
        if isinstance(t, str) and (n := normalize_title_for_matching(t))
    })
    payload = {"updated": time.time(), "titles": normalized}
    try:
        _FRONTEND_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FRONTEND_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, _FRONTEND_CACHE_PATH)
    except OSError as e:
        logger.warning("[owned_games] could not write owned-titles cache: %s", e)
        return 0
    logger.info(
        "[owned_games] stored %d frontend-supplied owned Steam title(s)",
        len(normalized),
    )
    return len(normalized)


def load_frontend_owned_titles() -> frozenset[str]:
    """Read the frontend-supplied owned titles (empty when absent/stale)."""
    try:
        data = json.loads(_FRONTEND_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    titles = data.get("titles") if isinstance(data, dict) else None
    if not isinstance(titles, list):
        return frozenset()
    return frozenset(t for t in titles if isinstance(t, str) and t)
def get_owned_titles(
    config: ConfigManager | None = None,
) -> frozenset[str]:
    """Get owned titles."""
    global _cache
    steam_path = find_steam_path(config)
    if steam_path is None:
        logger.debug("[owned_games] no Steam install found")
        return frozenset()
    fingerprint = _compute_fingerprint(Path(steam_path))
    if _cache is not None and _cache[0] == fingerprint:
        return _cache[1]
    titles = _scan_all_libraries(Path(steam_path))
    logger.info(
        "[owned_games] indexed %d Steam-native title(s) from %s",
        len(titles), steam_path,
    )
    _cache = (fingerprint, titles)
    return titles
def invalidate_cache() -> None:
    """Invalidate cache."""
    global _cache
    _cache = None
def _compute_fingerprint(steam_path: Path) -> _Fingerprint:
    """Compute fingerprint."""
    libfolders_vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    libfolders_mtime = _stat_mtime(libfolders_vdf)
    library_roots = _list_library_roots(steam_path)
    per_library: list[tuple[str, float | None]] = []
    for root in library_roots:
        steamapps = root / "steamapps"
        per_library.append((str(root), _stat_mtime(steamapps)))
    return (str(steam_path), libfolders_mtime, tuple(per_library))
def _stat_mtime(path: Path) -> float | None:
    """Stat mtime."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None

def _scan_all_libraries(steam_path: Path) -> frozenset[str]:
    """Scan all libraries."""
    titles: set[str] = set()
    for library_root in _list_library_roots(steam_path):
        try:
            titles.update(_titles_from_library(library_root))
        except OSError as e:
            logger.warning(
                "[owned_games] could not scan %s: %s",
                library_root, e,
            )
    return frozenset(titles)
def _list_library_roots(steam_path: Path) -> list[Path]:
    """List library roots."""
    roots: list[Path] = [steam_path]
    libfolders_vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if not libfolders_vdf.is_file():
        return roots
    try:
        content = libfolders_vdf.read_text(
            encoding="utf-8", errors="replace",
        )
    except OSError as e:
        logger.warning(
            "[owned_games] cannot read %s: %s", libfolders_vdf, e,
        )
        return roots
    for match in _LIBFOLDER_PATH_PATTERN.finditer(content):
        candidate = Path(match.group(1))
        if candidate == steam_path:
            continue
        if (candidate / "steamapps").is_dir():
            roots.append(candidate)
    return roots
def _titles_from_library(library_root: Path) -> set[str]:
    """Titles from library."""
    steamapps = library_root / "steamapps"
    if not steamapps.is_dir():
        return set()
    titles: set[str] = set()
    for manifest in steamapps.glob("appmanifest_*.acf"):
        title = _extract_name_from_manifest(manifest)
        if title:
            normalized = normalize_title_for_matching(title)
            if normalized:
                titles.add(normalized)
    return titles
def _extract_name_from_manifest(manifest: Path) -> str | None:
    """Extract name from manifest."""
    try:
        content = manifest.read_text(
            encoding="utf-8", errors="replace",
        )
    except OSError as e:
        logger.warning(
            "[owned_games] cannot read %s: %s", manifest, e,
        )
        return None
    match = _ACF_NAME_PATTERN.search(content)
    return match.group(1) if match else None
