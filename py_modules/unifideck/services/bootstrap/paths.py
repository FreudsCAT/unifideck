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

# Last-resort fallback if loginusers.vdf can't be parsed AND no real
# user directory exists under ``userdata/``. NEVER use this for
# real writes — ``"0"`` is Steam's guest / meta directory and any
# ``shortcuts.vdf`` we drop there is invisible to the running Steam
# client. The active-user resolver in ``unifideck.steam.steam_user``
# fails loudly when it returns this so callers can defer writes
# until Steam is logged in.
_USER_ID_GUEST = "0"

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


def _expand_config_path(config: ConfigManager, key: str, fallback: str) -> str:
    """Read a config path value and expand ``~`` to an absolute string."""
    return str(Path(config.get(key, fallback)).expanduser())


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
    plugin_dir: str
    launcher_path: str
    shortcuts_path: str
    games_map_path: str
    config_vdf_path: str
    loginusers_path: str
    grid_dir: str
    queue_file: str
    playtime_db: str
    local_save_root: str
    cloud_root: str | None
    # Rotating JSONL log of recent library syncs (started /
    # completed / cancelled). Consumed by ActivityLogService.
    activity_log: str

    @classmethod
    def from_config(
        cls, config: ConfigManager, plugin_dir: str | None = None,
    ) -> ServicePaths:
        """Resolve every path from ``config``, mkdir ``data_dir``.

        ``steam_root`` falls back to ``~/.steam/steam`` when
        Steam isn't found — keeps the plugin loadable on dev
        machines without a Steam install; services that actually
        need Steam must validate it themselves.
        """
        # Base directories — both expanded via Path.expanduser so
        # the config can use ``~`` and we still get an absolute path.
        data_dir = _expand_config_path(
            config, "paths.data_dir", _FALLBACK_PATHS["paths.data_dir"],
        )
        steam_root = _expand_config_path(
            config, "paths.steam_root", _DEFAULT_STEAM_ROOT,
        )
        Path(data_dir).mkdir(parents=True, exist_ok=True)

        # Resolve the plugin install directory + the launcher
        # binary inside it. ``plugin_dir`` is the value Decky
        # passed to the plugin's ``_main`` (e.g.
        # ``/home/deck/homebrew/plugins/Unifideck``); falls back
        # to the parent of this module's package root for dev
        # runs outside Decky. The launcher must always be
        # ``<plugin>/bin/unifideck-launcher`` — see ``bin/``.
        resolved_plugin_dir = (
            plugin_dir
            if plugin_dir
            else str(Path(__file__).resolve().parents[3])
        )
        launcher_path = str(
            Path(resolved_plugin_dir) / "bin" / "unifideck-launcher",
        )

        # Cache Path versions for the multi-segment joins below.
        steam_root_path = Path(steam_root)
        data_dir_path = Path(data_dir)
        # Resolve the active Steam user (NEVER ``"0"`` — that's the
        # guest / meta directory Steam ignores). Falls back to the
        # guest dir only if no real user exists yet (fresh Deck);
        # consumers should treat that case as "Steam not logged in"
        # and defer writes. See ``unifideck.steam.steam_user`` for
        # the detection layers (loginusers.vdf MostRecent → mtime).
        from unifideck.steam.steam_user import get_active_steam_user
        active_user = get_active_steam_user(steam_root_path) or _USER_ID_GUEST
        userdata_dir = steam_root_path / "userdata" / active_user
        config_dir = userdata_dir / "config"

        return cls(
            data_dir=data_dir,
            steam_root=steam_root,
            plugin_dir=resolved_plugin_dir,
            launcher_path=launcher_path,
            shortcuts_path=str(config_dir / "shortcuts.vdf"),
            games_map_path=_expand_config_path(
                config, "paths.games_map", _FALLBACK_PATHS["paths.games_map"],
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
            activity_log=str(data_dir_path / "sync_activity.log"),
        )
