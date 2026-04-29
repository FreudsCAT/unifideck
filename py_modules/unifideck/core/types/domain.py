"""core/types/domain.py — Domain dataclasses (Game, StoreInfo, CLITool).

  - Events add entries frequently → their own file
  - Results mirror service surface → grouped with dataclass base
  - Domain types are stable → rarely touched, grouped together

Reference: Technical Document v1.0 — Section 3.4.1 (Core types).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Game:
    """A single game entry as stored in Unifideck's unified library.

    This is the canonical representation after store-specific
    fields have been normalized. Store connectors convert their
    raw payloads into Game instances via a `_to_game()` helper.

    Fields:
      app_id: stable integer ID assigned by Unifideck (hash of
        store + store's game_id). Used as the Steam shortcut ID.
      store: canonical store name (see StoreEnum values)
      store_game_id: the store-native identifier
      title: display title
      installed: True if the game is currently on disk
      install_path: absolute path to the game's install directory,
        None if not installed
      exe_path: absolute path to the main executable, None if not
        resolved yet
      size_bytes: installed size (0 if unknown or not installed)
      tags: filter tags — see GameTag enum
      icon_url / hero_url / logo_url: artwork source URLs, used
        before ArtworkService materializes them into files
      metadata: free-form per-store payload kept for debugging
    """

    app_id: int
    store: str
    store_game_id: str
    title: str
    installed: bool = False
    install_path: str | None = None
    exe_path: str | None = None
    size_bytes: int = 0
    tags: list[str] = field(default_factory=list)
    icon_url: str | None = None
    hero_url: str | None = None
    logo_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoreInfo:
    """Metadata exposed by a store connector via `StoreBase.store_info`.

    This is the public face of a store — what the frontend displays
    in the "Stores" tab. It's a class-level constant on each store
    class, not per-instance, so the frontend can list available
    stores before any auth flow starts.
    """

    name: str                     # canonical ID (lowercase)
    display_name: str             # user-visible name
    auth_method: str              # "oauth", "shortcut", "manual", ...
    icon_asset: str               # path under assets/
    uses_wine: bool = False       # True if games need a Wine prefix
    supports_install: bool = True # False for xCloud-style streaming
    supports_cloud_saves: bool = False


@dataclass
class CLITool:
    """A command-line tool needed by a store connector.

    Used by `BinaryResolver` to find a tool across the 3-tier
    search (bundled → PATH → ~/.local/bin/<n>).
    """

    name: str                     # binary name (e.g. "legendary")
    search_paths: list[str] = field(default_factory=list)
    version_flag: str = "--version"
    min_version: str | None = None
