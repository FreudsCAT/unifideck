"""Plugin filesystem paths — derived from the plugin root.

OP-13a | py_modules/unifideck/services/bootstrap/paths.py

``ServicePaths`` is a frozen dataclass holding every path Unifideck
needs at runtime, derived from a single root (the Decky plugin
directory passed by Decky Loader at boot). Centralising path
construction here means changing one entry in the dataclass is enough
to relocate the entire on-disk footprint (useful for testing).

Built once at boot by ``ServicePaths.from_plugin_dir(plugin_dir)`` and
threaded through every service constructor via the container.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import ConfigManager


@dataclass
class ServicePaths:
    """Service paths."""

    data_dir: str
    steam_root: str
    shortcuts_path: str
    games_map_path: str
    config_vdf_path: str
    loginusers_path: str
    grid_dir: str
    queue_file: str
    playtime_db: str
    local_save_root: str
    cloud_root: str | None

    @classmethod
    def from_config(cls, config: ConfigManager) -> ServicePaths:
        """From config."""
        from unifideck.steam.library import find_steam_path

        data_dir = str(
            Path(
                config.get("paths.data_dir"),
            ).expanduser(),
        )
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        steam_root = find_steam_path(config) or str(Path("~/.steam/steam").expanduser())
        steam_root_path = Path(steam_root)
        data_dir_path = Path(data_dir)
        return cls(
            data_dir=data_dir,
            steam_root=steam_root,
            shortcuts_path=str(
                steam_root_path / "userdata" / "0" / "config" / "shortcuts.vdf",
            ),
            games_map_path=str(
                Path(
                    config.get("paths.games_map"),
                ).expanduser(),
            ),
            config_vdf_path=str(
                steam_root_path / "config" / "config.vdf",
            ),
            loginusers_path=str(
                steam_root_path / "config" / "loginusers.vdf",
            ),
            grid_dir=str(
                steam_root_path / "userdata" / "0" / "config" / "grid",
            ),
            queue_file=str(data_dir_path / "download_queue.json"),
            playtime_db=str(data_dir_path / "playtime.db"),
            local_save_root=str(data_dir_path / "saves"),
            cloud_root=config.get("cloud.root") or None,
        )
