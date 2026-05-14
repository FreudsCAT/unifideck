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

# Hardcoded fallbacks mirroring ``defaults/config.json``. Used when the
# defaults file failed to load (corrupt JSON, missing from install,
# permissions) AND the user's config has no override either. Without
# these, ``config.get(...)`` returns None and ``Path(None)`` crashes
# Layer 5 — taking down the whole plugin instead of degrading gracefully.
# Keep these in sync with defaults/config.json — the JSON is the source
# of truth, this dict is the resilience net.
_FALLBACK_PATHS = {
    "paths.data_dir": "~/.local/share/unifideck",
    "paths.games_map": "~/.local/share/unifideck/games.map",
}


@dataclass
class ServicePaths:
    """Frozen container of every Layer-1 path the plugin needs.

    Fields are populated once at boot via ``from_config`` and threaded
    through every service constructor — services never call
    ``find_steam_path`` or read the path config themselves, they just
    consume the ``ServicePaths`` instance.

    Attributes:
        data_dir: writable directory under ``~/.local/share/`` where
            Unifideck stores its own state (DB, queue, save cache).
        steam_root: Steam installation root (typically
            ``~/.local/share/Steam``).
        shortcuts_path: absolute path to ``shortcuts.vdf`` inside the
            Steam userdata directory.
        games_map_path: path to Unifideck's games-map JSON file.
        config_vdf_path: path to Steam's main ``config.vdf``.
        loginusers_path: path to Steam's ``loginusers.vdf`` (watched
            by ``AccountService`` for account-switch detection).
        grid_dir: directory where Steam stores per-shortcut artwork
            (capsule / hero / logo / icon files).
        queue_file: path to the download queue persistence file.
        playtime_db: path to the SQLite playtime database.
        local_save_root: directory where per-game cloud-save mirrors
            are cached.
        cloud_root: optional override for the cloud-save root
            (set via ``cloud.root`` in the user config); ``None``
            means use the default location under ``data_dir``.
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
        """Build a ``ServicePaths`` from the user configuration.

        Reads ``paths.data_dir`` and ``paths.games_map`` from the
        config, locates the Steam install root through
        ``find_steam_path`` (falling back to ``~/.steam/steam`` if
        Steam couldn't be auto-detected), and derives every other
        path from those two roots:

        * ``shortcuts.vdf``, ``config.vdf``, ``loginusers.vdf`` and
          the ``grid/`` artwork directory all live under
          ``<steam_root>/userdata/0/config/`` or
          ``<steam_root>/config/``;
        * ``download_queue.json``, ``playtime.db`` and ``saves/``
          all live directly under ``<data_dir>``.

        The data directory is created on the fly so subsequent
        service constructors can assume it exists.

        Args:
            config: live ``ConfigManager`` from which path settings
                are read.

        Returns:
            A fully-populated, ready-to-thread ``ServicePaths``.
        """
        from unifideck.steam.library import find_steam_path

        data_dir = str(
            Path(
                config.get(
                    "paths.data_dir",
                    _FALLBACK_PATHS["paths.data_dir"],
                ),
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
                    config.get(
                        "paths.games_map",
                        _FALLBACK_PATHS["paths.games_map"],
                    ),
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
