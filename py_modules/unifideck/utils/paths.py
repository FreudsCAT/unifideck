"""utils/paths.py — Centralised path resolution for game installations.

# OP-33a | py_modules/unifideck/utils/paths.py | Depends: (none)

Single source of truth for where Unifideck looks for installed
games: default install dirs per store, mounted SD cards / USB
drives, and optional user-configured custom paths.

The legacy module hardcoded ``~/.local/share/unifideck/...``
paths and a fixed list of store install directories. This
refactor:
  - Reads default install paths from ``stores.<n>.install_dir``.
  - Reads custom override from ``download.custom_path``.
  - Reads the SD-card mount root from ``paths.sd_card_root``.
  - Returns a deduplicated list of existing directories.

Pure helpers (no I/O):
  - ``expand``       : tilde + env-var expansion in one shot.
  - ``dedupe_paths`` : remove duplicates preserving order.

Filesystem helpers:
  - ``get_all_game_directories(config)`` : full discovery scan.
  - ``get_games_map_path(config)``       : the games.map
                                           location.
  - ``ensure_games_map_dir(config)``     : create the parent
                                           dir.

Reference: Technical Document v1.0 — Section 3.6.1 (games.map),
3.9 (ConfigManager), 5.6 (installation pipeline).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

# Where games.map lives by default. Steam Deck never relocates
# this without explicit user action, so the path is stable.
DEFAULT_GAMES_MAP = "~/.local/share/unifideck/games.map"

# Mount root for SD cards / external drives on the Steam Deck.
DEFAULT_SD_ROOT = "/run/media"

# — Legacy compatibility aliases ——————————————————————————————
# Old constant names kept working so legacy modules
# (``utils/__init__.py``, ``accounts/account_manager.py``, etc.)
# that ``from .paths import GAMES_MAP_PATH, DEFAULT_PATHS``
# continue to import successfully during the migration window.
# Both forms resolve to the same expanded value.
GAMES_MAP_PATH = str(Path(DEFAULT_GAMES_MAP).expanduser())
DEFAULT_PATHS = {
    store: str(Path(path).expanduser())
    for store, path in DEFAULT_INSTALL_DIRS.items()
}


# ── Pure helpers ─────────────────────────────────────────────


def expand(path: str) -> str:
    """Expand ``~`` and ``$VAR`` references in a path string.
    Pure function — no filesystem I/O. Returns an
    absolute path when the input is absolute or contains
    ``~``; a relative path unchanged otherwise. Uses
    ``os.path.expandvars`` for env-var substitution
    because ``pathlib.Path`` has no equivalent, then
    wraps through ``Path(...).expanduser()`` for tilde
    resolution.
    """
    return str(Path(os.path.expandvars(path)).expanduser())


def dedupe_paths(paths: list[str]) -> list[str]:
    """Remove duplicate paths, preserving first-occurrence order.
    Uses a seen-set for O(n) dedup. Path comparison is by
    resolved string — caller should pass already-expanded
    paths. Used after collecting paths from multiple
    sources (default dirs + SD-card mounts + user
    overrides) where the same directory can legitimately
    appear twice.
    """
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


# ── Config helper ────────────────────────────────────────────


def _cfg(
    config: ConfigManager | None, key: str, default: Any,
) -> Any:
    """Read a config value with a default fallback.
    Thin helper used throughout the module so the
    ``config is None`` check stays in one place. Returns
    ``default`` when config is None OR when the key is
    missing / empty.
    """
    if config is None:
        return default
    try:
        val = config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


# ── Filesystem helpers ───────────────────────────────────────


def get_all_game_directories(
    config: ConfigManager | None = None,
) -> list[str]:
    """Return every directory Unifideck should scan for installed games.
    Aggregates:
      - Each store's ``install_dir`` via ``_collect_game_dirs``
        (reads ``stores.<n>.install_dir`` or falls back to
        ``DEFAULT_INSTALL_DIRS``).
      - Custom user override ``download.custom_path`` when
        set.
      - Every mounted SD-card / USB drive via
        ``_scan_mount_root`` (looks under
        ``paths.sd_card_root`` or ``DEFAULT_SD_ROOT``).
    Each candidate path is expanded, existence-checked,
    deduped. Non-existent dirs silently skipped. Returns
    the final list in discovery order (Steam installs +
    store dirs before external drives) so the caller's
    first-hit detection picks the canonical install
    location.
    """
    dirs: list[str] = []

    # Store default install dirs
    dirs.extend(_collect_game_dirs(config))

    # Custom user override
    custom = _cfg(config, "download.custom_path", "")
    if custom:
        expanded = expand(custom)
        if os.path.isdir(expanded):
            dirs.append(expanded)

    # SD card / external drives
    dirs.extend(_scan_mount_root(config))

    return dedupe_paths(dirs)


def _collect_game_dirs(
    config: ConfigManager | None,
) -> list[str]:
    """Read per-store install dirs from config, fall back to defaults.
    Iterates ``DEFAULT_INSTALL_DIRS`` keys; for each,
    tries ``stores.<n>.install_dir`` from config,
    falls back to the default value. Returns the
    expanded paths in store-enumeration order. Missing
    store keys skipped (a store disabled in config
    shouldn't drag its install dir into the scan).
    """
    result: list[str] = []
    for store, default_path in DEFAULT_INSTALL_DIRS.items():
        raw = _cfg(config, f"stores.{store}.default_install_root", default_path)
        expanded = expand(raw)
        if os.path.isdir(expanded):
            result.append(expanded)
    return result


def _scan_mount_root(
    config: ConfigManager | None,
) -> list[str]:
    """Enumerate mounted volumes under the SD-card root.
    Reads ``paths.sd_card_root`` from config, falls back
    to ``DEFAULT_SD_ROOT``. Walks 2 levels via
    ``_scan_level2``. Returns existing directories only.
    Used by ``get_all_game_directories`` to include
    games installed on removable media.
    """
    root = expand(_cfg(config, "paths.sd_card_root", DEFAULT_SD_ROOT))
    if not os.path.isdir(root):
        return []
    return _scan_level2(root)


def _scan_level2(
    root: str,
) -> list[str]:
    """Return second-level subdirs of ``root``.
    SD cards typically layout as
    ``/run/media/deck/<volume>/<games-dir>/``. We scan
    two levels deep from the mount root so we pick up
    the inner games directory rather than just the
    volume. Empty list on missing root / permission
    error — SD cards often produce transient errors
    during mount/unmount.
    """
    result: list[str] = []
    try:
        for volume in os.scandir(root):
            if not volume.is_dir():
                continue
            try:
                for subdir in os.scandir(volume.path):
                    if subdir.is_dir():
                        result.append(subdir.path)
            except OSError:
                pass
    except OSError:
        pass
    return result


def get_games_map_path(
    config: ConfigManager | None = None,
) -> str:
    """Return the ``games.map`` file path, expanded.
    Reads ``paths.games_map`` from config, falls back to
    ``DEFAULT_GAMES_MAP``. Always returns an absolute
    path. Creates no directories — caller calls
    ``ensure_games_map_dir`` when write access is about
    to be needed.
    """
    raw = _cfg(config, "paths.games_map", DEFAULT_GAMES_MAP)
    return expand(raw)


def ensure_games_map_dir(
    config: ConfigManager | None = None,
) -> str:
    """Create the parent directory of ``games.map`` if missing.
    Returns the resolved ``games.map`` path for
    convenience (so callers can ``ensure_games_map_dir()``
    and use the result directly without a second call to
    ``get_games_map_path``). Uses
    ``Path.mkdir(parents=True, exist_ok=True)`` — no-op
    when the dir already exists.
    """
    gm_path = get_games_map_path(config)
    Path(gm_path).parent.mkdir(parents=True, exist_ok=True)
    return gm_path
