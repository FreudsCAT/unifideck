"""services/bootstrap/paths.py — Filesystem paths resolved once at boot.

Single place that derives every filesystem path the plugin
uses from ``ConfigManager``. Services read from a
``ServicePaths`` instance rather than reconstructing paths —
guarantees the plugin agrees on where data lives, gives one
place to stub in tests, makes the ``ConfigManager`` dependency
explicit at boot rather than diffused through every ctor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

# Default fallback if Steam isn't installed (e.g. dev environment)
_DEFAULT_STEAM_ROOT = str(Path("~/.steam/steam").expanduser())

# TODO: revisit — consider auto-detection via loginusers.vdf (staging approach)
# Currently we hardcode the primary Steam Deck user ID "0".
_USER_ID = "0"


@dataclass
class ServicePaths:
    """All filesystem paths derived from the user environment.

    Built once by ``ServicePaths.from_config`` at startup.
    Field names match the service attribute they feed into
    (``shortcuts_path`` → ShortcutService, ``queue_file`` →
    DownloadService, etc.) so the wiring table in
    ``service_defs.py`` can reference them by name.
    """

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
        """Resolve every path from ``config``, mkdir ``data_dir``.

        ``steam_root`` falls back to ``~/.steam/steam`` when
        Steam isn't found — keeps the plugin loadable on dev
        machines without a Steam install; services that actually
        need Steam must validate it themselves.
        """
        # Base directories
        data_dir = config.get("paths.data_dir", str(Path("~/.config/unifideck").expanduser()))
        steam_root = config.get("paths.steam_root", _DEFAULT_STEAM_ROOT)

        # Ensure data directory exists
        Path(data_dir).mkdir(parents=True, exist_ok=True)

        # Steam userdata paths
        userdata_dir = str(Path(steam_root) / "userdata" / _USER_ID)
        config_dir = str(Path(userdata_dir) / "config")
        shortcuts_path = str(Path(config_dir) / "shortcuts.vdf")
        config_vdf_path = str(Path(config_dir) / "localconfig.vdf")
        loginusers_path = str(Path(steam_root) / "config" / "loginusers.vdf")
        grid_dir = str(Path(config_dir) / "grid")

        # Unifideck data files
        games_map_path = str(Path(data_dir) / "games.map")
        queue_file = str(Path(data_dir) / "download_queue.json")
        playtime_db = str(Path(data_dir) / "playtime.db")
        local_save_root = str(Path(data_dir) / "saves")
        cloud_root = config.get("cloud_saves.remote_root")

        return cls(
            data_dir=data_dir,
            steam_root=steam_root,
            shortcuts_path=shortcuts_path,
            games_map_path=games_map_path,
            config_vdf_path=config_vdf_path,
            loginusers_path=loginusers_path,
            grid_dir=grid_dir,
            queue_file=queue_file,
            playtime_db=playtime_db,
            local_save_root=local_save_root,
            cloud_root=cloud_root,
        )
