"""unifideck.utils — Path resolution and helpers.

Refactored utils/paths.py exposes the new constants
`DEFAULT_GAMES_MAP` and `DEFAULT_INSTALL_DIRS`, plus the
config-aware `get_all_game_directories(config)`. Legacy names
`GAMES_MAP_PATH` and `DEFAULT_PATHS` are preserved as expanded
constants in paths.py for backward compatibility.
"""
from .paths import (  # noqa: F401
    # New constants
    DEFAULT_GAMES_MAP,
    DEFAULT_INSTALL_DIRS,
    DEFAULT_PATHS,
    DEFAULT_SD_ROOT,
    # Legacy constants (kept for v0.6.x callers)
    GAMES_MAP_PATH,
    dedupe_paths,
    ensure_games_map_dir,
    expand,
    # Functions
    get_all_game_directories,
    get_games_map_path,
)
