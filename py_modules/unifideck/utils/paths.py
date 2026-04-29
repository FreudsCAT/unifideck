"""utils/paths.py — Centralized path resolution for game installations.

Refactor of legacy ``utils/paths.py`` (130 lines). Provides a
single source of truth for where Unifideck looks for installed
games: default install dirs per store, mounted SD cards/USB
drives, and optional user-configured custom paths.

The legacy module hardcoded ``~/.local/share/unifideck/...``
paths and a fixed list of store install directories. This
refactor:

- Reads default install paths from ``stores.<n>.install_dir``
- Reads custom override from ``download.custom_path``
- Reads the SD card mount root from ``paths.sd_card_root``
- Returns a deduplicated list of existing directories

Pure helpers (no I/O):

- ``expand`` : tilde + env-var expansion in one shot
- ``dedupe_paths`` : remove duplicates preserving order

Filesystem helpers:

- ``get_all_game_directories(config)`` : full discovery scan
- ``get_games_map_path(config)`` : the games.map location
- ``ensure_games_map_dir(config)`` : create the parent dir

Reference: Technical Document v1.0 — Section 3.6.1 (games.map),
3.9 (ConfigManager), 5.6 (installation pipeline).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_helpers import get_cfg

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)

# Default install directories per store, used when no override
# is set in ``stores.<n>.install_dir``. These match the legacy
# paths so existing user installs are still discovered.
DEFAULT_INSTALL_DIRS = {
    "epic": "~/Games/Epic",
    "gog": "~/GOG Games",
    "amazon": "~/Games/Amazon",
    "microsoft": "~/Games/Microsoft",
    "ubisoft": "~/Games/Ubisoft",
}

# Where the games.map lives by default. Steam Deck never
# relocates this without explicit user action, so the path is
# stable.
DEFAULT_GAMES_MAP = "~/.local/share/unifideck/games.map"

# Mount root for SD cards / external drives on the Steam Deck
DEFAULT_SD_ROOT = "/run/media"

# ── Legacy compatibility aliases ──────────────────────────────
# Keep the old constant names working so legacy modules
# (utils/__init__.py, accounts/account_manager.py, etc.) that
# ``from .paths import GAMES_MAP_PATH, DEFAULT_PATHS`` continue
# to import successfully during the migration window. Both forms
# resolve to the same expanded value.
GAMES_MAP_PATH = str(Path(DEFAULT_GAMES_MAP).expanduser())
DEFAULT_PATHS = {
    store: str(Path(path).expanduser())
    for store, path in DEFAULT_INSTALL_DIRS.items()
}


# ══════════════════════════════════════════════════════════════
# Pure helpers
# ══════════════════════════════════════════════════════════════


def expand(path: str) -> str:
    """Expand ``~`` and ``$VAR`` references in a path string.

    Pure function — no filesystem I/O. Returns an absolute path
    (when the input is absolute or contains ``~``) or a relative
    path unchanged.

    Uses ``os.path.expandvars`` for env var substitution because
    ``pathlib.Path`` has no equivalent. The result is then
    wrapped through ``Path(...).expanduser()`` for the tilde
    resolution.
    """
    return str(Path(os.path.expandvars(path)).expanduser())


def dedupe_paths(paths: list[str]) -> list[str]:
    """Remove duplicate paths preserving order.

    Two paths are considered equal if their normalized form
    (``os.path.normpath``) matches. Useful when merging
    discovery results from multiple sources that may overlap.
    """
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        norm = os.path.normpath(p)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(p)
    return out


def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Legacy alias for backward compatibility. Delegates to `get_cfg`."""
    return get_cfg(config, key, default)


# ══════════════════════════════════════════════════════════════
# Filesystem discovery
# ══════════════════════════════════════════════════════════════


def get_all_game_directories(config: ConfigManager | None = None) -> list[str]:
    """Return every directory that may contain installed games.

    Combines:

    1. Per-store install dirs from ``stores.<n>.install_dir``
       (or ``DEFAULT_INSTALL_DIRS`` as fallback)
    2. The user's custom path from ``download.custom_path``
    3. SD card / external drive mounts under
       ``paths.sd_card_root`` — scans 2 levels deep for
       ``Games/`` and ``GOG Games/`` folders

    Only returns directories that actually exist on disk.
    Result is deduplicated.
    """
    candidates: list[str] = []

    # 1. Per-store install dirs
    for store, default in DEFAULT_INSTALL_DIRS.items():
        path = _cfg(config, f"stores.{store}.install_dir", default)
        candidates.append(expand(path))

    # 2. Custom user path
    custom = get_cfg(config, "download.custom_path", "")
    if custom:
        candidates.append(expand(custom))

    # 3. SD card / external drive scan
    media_root = get_cfg(config, "paths.sd_card_root", DEFAULT_SD_ROOT)
    candidates.extend(_scan_mount_root(media_root))

    # Filter to existing dirs and dedupe
    existing = [p for p in candidates if Path(p).is_dir()]
    return dedupe_paths(existing)


def _collect_game_dirs(parent_path: Path) -> list[str]:
    """Return ``Games/`` and ``GOG Games/`` subdirs of ``parent_path``.

    Helper for ``_scan_mount_root`` — factored out so the scan
    loop stays shallow. Symlinks are skipped to avoid loops.
    """
    found: list[str] = []
    for sub in ("Games", "GOG Games"):
        p = parent_path / sub
        if p.is_dir() and not p.is_symlink():
            found.append(str(p))
    return found


def _scan_level2(level1_path: Path) -> list[str]:
    """Scan the level-2 subtree under ``level1_path`` for game dirs.

    Some Decks (LUKS-on-external SSD setups notably) mount
    game partitions one level deeper than the standard
    ``/run/media/<user>/<mount>/Games`` layout — this helper
    handles the ``<mount>/<level2>/Games`` case. Returns an
    empty list on any I/O error (mount disappeared between
    listings, permissions trouble).
    """
    found: list[str] = []
    try:
        for level2_path in level1_path.iterdir():
            if (
                not level2_path.is_dir()
                or level2_path.is_symlink()
            ):
                continue
            found.extend(_collect_game_dirs(level2_path))
    except OSError:
        # best-effort operation; failure is non-fatal here
        pass
    return found


def _scan_mount_root(root: str) -> list[str]:
    """Walk ``/run/media/<user>/<mount>/`` looking for game folders.

    Returns paths matching ``<mount>/Games/`` or
    ``<mount>/GOG Games/`` where they exist. Errors are logged
    at debug level only — a missing ``/run/media`` on a
    non-Deck system is normal.

    SECURITY/ROBUSTNESS: symbolic links are skipped at every
    level. A symlink loop (e.g. ``Games/Other -> Games/``) would
    otherwise cause the scan to recurse indefinitely or return
    duplicate paths. The dedupe pass at the caller would mask
    the duplicates but the wasted CPU on a symlink loop could
    freeze sync.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    found: list[str] = []
    try:
        for level1_path in root_path.iterdir():
            if (
                not level1_path.is_dir()
                or level1_path.is_symlink()
            ):
                continue
            # /run/media/<level1>/Games or GOG Games
            found.extend(_collect_game_dirs(level1_path))
            # /run/media/<level1>/<level2>/Games (some Decks)
            found.extend(_scan_level2(level1_path))
    except OSError as e:
        logger.debug(
            "[paths] mount scan failed on %s: %s", root, e,
        )
    return found


# ══════════════════════════════════════════════════════════════
# games.map location
# ══════════════════════════════════════════════════════════════


def get_games_map_path(config: ConfigManager | None = None) -> str:
    """Return the absolute path to the games.map file.

    Reads ``paths.games_map`` from config if set, otherwise
    falls back to ``~/.local/share/unifideck/games.map``. Tilde
    and env vars in the configured path are expanded.
    """
    raw = get_cfg(config, "paths.games_map", DEFAULT_GAMES_MAP)
    return expand(raw)


def ensure_games_map_dir(config: ConfigManager | None = None) -> str | None:
    """Create the parent directory for games.map if missing.

    Returns the directory path on success, None on failure.
    Idempotent — safe to call on every plugin start.
    """
    path = Path(get_games_map_path(config))
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        return str(parent)
    except OSError as e:
        logger.warning(
            "[paths] mkdir %s failed: %s", parent, e,
        )
        return None
