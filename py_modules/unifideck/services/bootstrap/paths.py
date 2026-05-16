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
        # Base directories — both expanded via Path.expanduser so
        # the config can use ``~`` and we still get an absolute path.
        data_dir = str(
            Path(
                config.get(
                    "paths.data_dir",
                    _FALLBACK_PATHS["paths.data_dir"],
                ),
            ).expanduser(),
        )
        steam_root = str(
            Path(
                config.get("paths.steam_root", _DEFAULT_STEAM_ROOT),
            ).expanduser(),
        )
        Path(data_dir).mkdir(parents=True, exist_ok=True)

        # Cache Path versions for the multi-segment joins below.
        steam_root_path = Path(steam_root)
        data_dir_path = Path(data_dir)
        userdata_dir = steam_root_path / "userdata" / _USER_ID
        config_dir = userdata_dir / "config"

        return cls(
            data_dir=data_dir,
            steam_root=steam_root,
            shortcuts_path=str(config_dir / "shortcuts.vdf"),
            games_map_path=str(
                Path(
                    config.get(
                        "paths.games_map",
                        _FALLBACK_PATHS["paths.games_map"],
                    ),
                ).expanduser(),
            ),
            config_vdf_path=str(config_dir / "localconfig.vdf"),
            loginusers_path=str(
                steam_root_path / "config" / "loginusers.vdf",
            ),
            grid_dir=str(config_dir / "grid"),
            queue_file=str(data_dir_path / "download_queue.json"),
            playtime_db=str(data_dir_path / "playtime.db"),
            local_save_root=str(data_dir_path / "saves"),
            cloud_root=config.get("cloud_saves.remote_root") or None,
        )
