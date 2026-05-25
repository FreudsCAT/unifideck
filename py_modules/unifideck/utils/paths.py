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

import contextlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

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

# Root game directory — always checked so internal storage
# shows up even before any per-store subdirectories exist.
DEFAULT_GAMES_ROOT = "~/Games"

# Where the games.map lives by default. Steam Deck never
# relocates this without explicit user action, so the path is
# stable.
DEFAULT_GAMES_MAP = "~/.local/share/unifideck/games.map"

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

    # 1. Root games directory — always available as internal storage.
    #    Create it if missing so internal storage is never empty.
    games_root = expand(DEFAULT_GAMES_ROOT)
    _ensure_dir(games_root)
    candidates.append(games_root)

    # 2. Per-store install dirs
    for store, default in DEFAULT_INSTALL_DIRS.items():
        path = _cfg(config, f"stores.{store}.install_dir", default)
        candidates.append(expand(path))

    # 3. Custom user path
    custom = get_cfg(config, "download.custom_path", "")
    if custom:
        candidates.append(expand(custom))

    # 4. External drives — scan every writable non-system mount
    #    from /proc/mounts for Game directories.  No hardcoded
    #    paths — whatever is mounted and writable gets checked.
    candidates.extend(_scan_external_mounts())

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



def _ensure_dir(path: str) -> None:
    """Create a directory if it doesn't exist. Idempotent."""
    Path(path).mkdir(parents=True, exist_ok=True)


def _device_id(path: str) -> int:
    """Return st_dev of *path*, or 0 on error."""
    try:
        return os.stat(path).st_dev
    except OSError:
        return 0


def _scan_external_mounts() -> list[str]:
    """Scan every writable external mount for game directories.

    Reads ``/proc/mounts`` to discover mount points — no hardcoded
    paths.  Skips the device that contains ``$HOME`` (internal
    storage) and non-storage filesystem types.  For each remaining
    mount, looks for ``Games/`` and ``GOG Games/`` subdirectories
    at the mount root and one level deeper (some setups mount
    partitions inside a parent directory).

    Symlinks are skipped at every level to avoid loops.
    """
    home_dev = _device_id(str(Path.home()))
    found: list[str] = []

    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mp = parts[1]
                fstype = parts[2]
                if fstype in _SKIP_FSTYPES:
                    continue
                if mp.startswith(_VIRTUAL_PREFIXES):
                    continue
                mp_path = Path(mp)
                if not mp_path.is_dir():
                    continue
                if _device_id(mp) == home_dev:
                    continue  # internal — already covered
                # Direct subdirectories at mount root
                found.extend(_collect_game_dirs(mp_path))
                # One level deeper (e.g. /mount/<label>/Games)
                with contextlib.suppress(OSError):
                    for child in mp_path.iterdir():
                        if child.is_dir() and not child.is_symlink():
                            found.extend(_collect_game_dirs(child))
    except OSError as e:
        logger.debug("[paths] external mount scan failed: %s", e)

    return found


_SKIP_FSTYPES = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup",
    "cgroup2", "pstore", "bpf", "debugfs", "tracefs", "hugetlbfs",
    "ramfs", "overlay", "squashfs", "fuse.gvfsd-fuse",
    "fuse.portal", "securityfs", "configfs", "efivarfs",
})


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
