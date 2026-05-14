"""Game compatibility ratings (ProtonDB, Steam Deck Verified) and Proton tools management."""

from __future__ import annotations

from .library import (
    BackgroundCompatFetcher,
    CompatLibrary,
    CompatRating,
    fetch_deck_verified,
    fetch_protondb_rating,
    get_compat_for_title,
    load_compat_cache,
    prefetch_compat,
    save_compat_cache,
    search_steam_store,
)
from .proton_helpers import (
    CompatToolResult,
    ProtonToolsManager,
    get_compat_tool_for_app,
    get_compat_tool_for_game,
    get_saved_proton_tool,
    is_linux_runtime,
    resolve_proton_path,
    restore_compat_tool,
    save_proton_setting,
    temporarily_clear_compat_tool,
)

__all__ = [
    "BackgroundCompatFetcher",
    "CompatLibrary",
    "CompatRating",
    "CompatToolResult",
    "ProtonToolsManager",
    "fetch_deck_verified",
    "fetch_protondb_rating",
    "get_compat_for_title",
    "get_compat_tool_for_app",
    "get_compat_tool_for_game",
    "get_saved_proton_tool",
    "is_linux_runtime",
    "load_compat_cache",
    "prefetch_compat",
    "resolve_proton_path",
    "restore_compat_tool",
    "save_compat_cache",
    "save_proton_setting",
    "search_steam_store",
    "temporarily_clear_compat_tool",
]

