# OP-05c | core/types/domain.py | Depends: (none)
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Game:
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
    name: str
    display_name: str
    auth_method: str
    icon_asset: str
    uses_wine: bool = False
    supports_install: bool = True
    supports_cloud_saves: bool = False


@dataclass
class CLITool:
    name: str
    search_paths: list[str] = field(default_factory=list)
    version_flag: str = "--version"
    min_version: str | None = None
