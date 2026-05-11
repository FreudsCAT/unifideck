"""
Ubisoft store configuration — frozen dataclass with deferred path resolution.

OP-55b | py_modules/unifideck/stores/ubisoft/config.py

``UbisoftConfig`` is a frozen dataclass holding every tunable parameter
of the Ubisoft sub-package: data directories, prefix locations, installer
URL, UPC binary names, credential file list, Wine system users, Steam
filtering toggle, etc.

The class exposes two kinds of fields:

* **Raw fields** (e.g. ``data_dir``, ``prefixes_dir``) — strings as
  configured, may contain ``~``;
* **Expanded properties** (e.g. ``data_dir_expanded``) — same value with
  ``~`` resolved at access time. We defer expansion to property access
  so that a user changing ``$HOME`` mid-session sees the new value.

Configuration is loaded via ``from_config_manager(config)`` which walks
the ``_FIELD_SPECS`` registry and parses each key from the
``stores.ubisoft.*`` namespace of the user config, falling back to the
hard-coded default if the key is missing or has the wrong type.

The dataclass is intentionally ``frozen=True``: any mutation must go
through a new ``UbisoftConfig`` instance, which the ``store`` re-instantiates
when the user changes settings.
"""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar
from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from ...config import ConfigManager
logger = logging.getLogger(__name__)
_DEFAULT_DATA_DIR = "~/.local/share/unifideck"
_DEFAULT_ID_MAP_FILE = "~/.local/share/unifideck/ubisoft_id_map.json"
_DEFAULT_VISIBLE_GAMES_FILE = "~/.local/share/unifideck/ubisoft_visible_games.json"
_DEFAULT_PREFIXES_DIR = "~/.local/share/unifideck/prefixes/ubisoft"
_DEFAULT_INSTALLER_CACHE_DIR = "~/.local/share/unifideck/ubisoft_installer_cache"
_DEFAULT_UPC_SESSION_FILE = "~/.local/share/unifideck/ubisoft_upc_session.txt"
_DEFAULT_GAME_ID_DB_FILE = "~/.local/share/unifideck/ubisoft_game_db.txt"
_DEFAULT_DEFAULT_INSTALL_BASE = "~/Games/Ubisoft"
_DEFAULT_SDCARD_INSTALL_BASE = "/run/media/mmcblk0p1/Games/Ubisoft"
_DEFAULT_INSTALLER_URL = (
    "https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe"
)
_DEFAULT_INSTALLER_FILENAME = "UbisoftConnectInstaller.exe"
_DEFAULT_GAME_ID_DB_URL = (
    "https://raw.githubusercontent.com/iArtorias/ubisoft_game_ids/main/UBI_GAMES.txt"
)
_DEFAULT_UPC_RELATIVE_PATH = (
    "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"
)
_DEFAULT_UPC_CONNECT_RELATIVE_PATH = (
    "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/UbisoftConnect.exe"
)
_DEFAULT_CONFIGURATIONS_RELATIVE_PATH = (
    "drive_c/users/steamuser/AppData/Local/"
    "Ubisoft Game Launcher/cache/configuration/configurations"
)
_DEFAULT_OWNERSHIP_RELATIVE_PATH = (
    "drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher/cache/ownership"
)
_UBI_CONFIG_PREFIX = "stores.ubisoft"


@dataclass(frozen=True)
class UbisoftConfig:
    """Ubisoft config."""

    _FIELD_SPECS: ClassVar[tuple]
    data_dir: str = _DEFAULT_DATA_DIR

    id_map_file: str = _DEFAULT_ID_MAP_FILE
    visible_games_file: str = _DEFAULT_VISIBLE_GAMES_FILE
    prefixes_dir: str = _DEFAULT_PREFIXES_DIR
    installer_cache_dir: str = _DEFAULT_INSTALLER_CACHE_DIR
    upc_session_file: str = _DEFAULT_UPC_SESSION_FILE
    game_id_db_file: str = _DEFAULT_GAME_ID_DB_FILE
    default_install_base: str = _DEFAULT_DEFAULT_INSTALL_BASE
    sdcard_install_base: str = _DEFAULT_SDCARD_INSTALL_BASE
    template_prefix_name: str = ".template"
    auth_prefix_name: str = ".upc-auth"
    auth_shortcut_store_id: str = "ubisoft:upc-auth"
    auth_shortcut_launch_wait_ms: int = 1500
    installer_url: str = _DEFAULT_INSTALLER_URL
    installer_filename: str = _DEFAULT_INSTALLER_FILENAME
    bootstrap_marker: str = "unifideck_ubisoft_bootstrap.marker"
    game_id_db_url: str = _DEFAULT_GAME_ID_DB_URL
    game_id_db_max_age_seconds: int = 7 * 24 * 3600
    upc_relative_path: str = _DEFAULT_UPC_RELATIVE_PATH
    upc_connect_relative_path: str = _DEFAULT_UPC_CONNECT_RELATIVE_PATH
    configurations_relative_path: str = _DEFAULT_CONFIGURATIONS_RELATIVE_PATH
    ownership_relative_path: str = _DEFAULT_OWNERSHIP_RELATIVE_PATH
    upc_credential_files: tuple[str, ...] = (
        "ConnectSecureStorage.dat",
        "user.dat",
    )
    upc_local_subdir: str = os.path.join(
        "AppData",
        "Local",
        "Ubisoft Game Launcher",
    )
    upc_auth_cache_artifacts: tuple[str, ...] = (
        "settings.yaml",
        os.path.join("cache", "configuration"),
        os.path.join("cache", "settings"),
        os.path.join("cache", "ulcf"),
        os.path.join(
            "cache",
            "http2",
            "Default",
            "Network",
        ),
        os.path.join(
            "cache",
            "http2",
            "Default",
            "Local Storage",
        ),
        os.path.join(
            "cache",
            "http2",
            "Default",
            "IndexedDB",
        ),
        os.path.join(
            "cache",
            "http2",
            "Default",
            "Preferences",
        ),
        os.path.join(
            "cache",
            "http2",
            "Default",
            "Session Storage",
        ),
        os.path.join("cache", "ownership"),
    )
    wine_system_users: tuple[str, ...] = (
        "Public",
        "All Users",
        "Default",
        "Default User",
    )
    filter_steam_linked: bool = True
    steam_library_cross_ref: bool = False

    @property
    def data_dir_expanded(self) -> str:
        """Data dir expanded."""
        return os.path.expanduser(self.data_dir)

    @property
    def id_map_file_expanded(self) -> str:
        """Id map file expanded."""
        return os.path.expanduser(self.id_map_file)

    @property
    def visible_games_file_expanded(self) -> str:
        """Visible games file expanded."""
        return os.path.expanduser(self.visible_games_file)

    @property
    def prefixes_dir_expanded(self) -> str:
        """Prefixes dir expanded."""
        return os.path.expanduser(self.prefixes_dir)

    @property
    def template_dir_expanded(self) -> str:
        """Template dir expanded."""
        return os.path.join(
            self.prefixes_dir_expanded,
            self.template_prefix_name,
        )

    @property
    def auth_prefix_dir_expanded(self) -> str:
        """Auth prefix dir expanded."""
        return os.path.join(
            self.prefixes_dir_expanded,
            self.auth_prefix_name,
        )

    @property
    def installer_cache_dir_expanded(self) -> str:
        """Installer cache dir expanded."""
        return os.path.expanduser(self.installer_cache_dir)

    @property
    def upc_session_file_expanded(self) -> str:
        """Upc session file expanded."""
        return os.path.expanduser(self.upc_session_file)

    @property
    def game_id_db_file_expanded(self) -> str:
        """Game ID db file expanded."""
        return os.path.expanduser(self.game_id_db_file)

    @property
    def default_install_base_expanded(self) -> str:
        """Default install base expanded."""
        return os.path.expanduser(self.default_install_base)

    def iter_game_prefix_paths(self) -> list[str]:
        """Iter game prefix paths."""
        prefixes_dir = self.prefixes_dir_expanded
        if not os.path.isdir(prefixes_dir):
            return []
        result: list[str] = []
        try:
            for entry in os.listdir(prefixes_dir):
                if entry.startswith("."):
                    continue
                candidate = os.path.join(prefixes_dir, entry)
                if os.path.isdir(candidate):
                    result.append(candidate)
        except OSError:
            pass
        return result

    @staticmethod
    def _parse_str(
        config: ConfigManager | None,
        key: str,
        default: str,
    ) -> str:
        """Parse str."""
        val = get_cfg(
            config,
            f"{_UBI_CONFIG_PREFIX}.{key}",
            default,
        )
        return str(val).strip() if val is not None else default

    @staticmethod
    def _parse_int(
        config: ConfigManager | None,
        key: str,
        default: int,
    ) -> int:
        """Parse int."""
        val = get_cfg(
            config,
            f"{_UBI_CONFIG_PREFIX}.{key}",
            default,
        )
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_tuple(
        config: ConfigManager | None,
        key: str,
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Parse tuple."""
        val = get_cfg(
            config,
            f"{_UBI_CONFIG_PREFIX}.{key}",
            None,
        )
        if not isinstance(val, list):
            return default
        filtered = [str(x) for x in val if isinstance(x, str) and x]
        return tuple(filtered) if filtered else default

    @staticmethod
    def _parse_bool(
        config: ConfigManager | None,
        key: str,
        default: bool,
    ) -> bool:
        """Parse bool."""
        val = get_cfg(
            config,
            f"{_UBI_CONFIG_PREFIX}.{key}",
            default,
        )
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            lowered = val.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
        return default

    @classmethod
    def from_config_manager(
        cls,
        config: ConfigManager | None,
    ) -> UbisoftConfig:
        """From config manager."""
        kwargs: dict[str, Any] = {}
        for field_name, key, parser, default in cls._FIELD_SPECS:
            kwargs[field_name] = parser(config, key, default)
        return cls(**kwargs)

    def describe(self) -> str:
        """Describe."""
        return (
            f"UbisoftConfig("
            f"prefixes_dir={self.prefixes_dir}, "
            f"install_base={self.default_install_base}, "
            f"installer_url={self.installer_url[:40]}…)"
        )


UbisoftConfig._FIELD_SPECS = (
    ("data_dir", "data_dir", UbisoftConfig._parse_str, _DEFAULT_DATA_DIR),
    ("id_map_file", "id_map_file", UbisoftConfig._parse_str, _DEFAULT_ID_MAP_FILE),
    (
        "visible_games_file",
        "visible_games_file",
        UbisoftConfig._parse_str,
        _DEFAULT_VISIBLE_GAMES_FILE,
    ),
    ("prefixes_dir", "prefixes_dir", UbisoftConfig._parse_str, _DEFAULT_PREFIXES_DIR),
    (
        "installer_cache_dir",
        "installer_cache_dir",
        UbisoftConfig._parse_str,
        _DEFAULT_INSTALLER_CACHE_DIR,
    ),
    (
        "upc_session_file",
        "upc_session_file",
        UbisoftConfig._parse_str,
        _DEFAULT_UPC_SESSION_FILE,
    ),
    (
        "game_id_db_file",
        "game_id_db_file",
        UbisoftConfig._parse_str,
        _DEFAULT_GAME_ID_DB_FILE,
    ),
    (
        "default_install_base",
        "default_install_base",
        UbisoftConfig._parse_str,
        _DEFAULT_DEFAULT_INSTALL_BASE,
    ),
    (
        "sdcard_install_base",
        "sdcard_install_base",
        UbisoftConfig._parse_str,
        _DEFAULT_SDCARD_INSTALL_BASE,
    ),
    (
        "template_prefix_name",
        "template_prefix_name",
        UbisoftConfig._parse_str,
        ".template",
    ),
    ("auth_prefix_name", "auth_prefix_name", UbisoftConfig._parse_str, ".upc-auth"),
    (
        "auth_shortcut_store_id",
        "auth_shortcut_store_id",
        UbisoftConfig._parse_str,
        "ubisoft:upc-auth",
    ),
    (
        "auth_shortcut_launch_wait_ms",
        "auth_shortcut_launch_wait_ms",
        UbisoftConfig._parse_int,
        1500,
    ),
    (
        "installer_url",
        "installer_url",
        UbisoftConfig._parse_str,
        _DEFAULT_INSTALLER_URL,
    ),
    (
        "installer_filename",
        "installer_filename",
        UbisoftConfig._parse_str,
        _DEFAULT_INSTALLER_FILENAME,
    ),
    (
        "bootstrap_marker",
        "bootstrap_marker",
        UbisoftConfig._parse_str,
        "unifideck_ubisoft_bootstrap.marker",
    ),
    (
        "game_id_db_url",
        "game_id_db_url",
        UbisoftConfig._parse_str,
        _DEFAULT_GAME_ID_DB_URL,
    ),
    (
        "game_id_db_max_age_seconds",
        "game_id_db_max_age_seconds",
        UbisoftConfig._parse_int,
        7 * 24 * 3600,
    ),
    (
        "upc_relative_path",
        "upc_relative_path",
        UbisoftConfig._parse_str,
        _DEFAULT_UPC_RELATIVE_PATH,
    ),
    (
        "upc_connect_relative_path",
        "upc_connect_relative_path",
        UbisoftConfig._parse_str,
        _DEFAULT_UPC_CONNECT_RELATIVE_PATH,
    ),
    (
        "configurations_relative_path",
        "configurations_relative_path",
        UbisoftConfig._parse_str,
        _DEFAULT_CONFIGURATIONS_RELATIVE_PATH,
    ),
    (
        "ownership_relative_path",
        "ownership_relative_path",
        UbisoftConfig._parse_str,
        _DEFAULT_OWNERSHIP_RELATIVE_PATH,
    ),
    (
        "upc_credential_files",
        "upc_credential_files",
        UbisoftConfig._parse_tuple,
        ("ConnectSecureStorage.dat", "user.dat"),
    ),
    (
        "wine_system_users",
        "wine_system_users",
        UbisoftConfig._parse_tuple,
        ("Public", "All Users", "Default", "Default User"),
    ),
    ("filter_steam_linked", "filter_steam_linked", UbisoftConfig._parse_bool, True),
    (
        "steam_library_cross_ref",
        "steam_library_cross_ref",
        UbisoftConfig._parse_bool,
        False,
    ),
)
