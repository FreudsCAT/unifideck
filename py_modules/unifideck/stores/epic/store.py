"""store.py — Public ``EpicStore`` (StoreBase implementation).

# OP-48a | py_modules/unifideck/stores/epic/store.py | Depends: (none)

Façade that wires legendary, library, install, updates, exe-resolver
and auth into a single :class:`StoreBase` subclass.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from ...auth.browser import OAuthBrowserMonitor
from ...auth.orchestrator import AuthOrchestrator
from ...core.binaries import read_cli_timeouts
from ...core.types import (
    AuthResult,
    CLITool,
    Events,
    Game,
    InstallResult,
    Result,
    StoreInfo,
)
from ...security import emit_external_auth_check_failed
from ...utils.config_helpers import get_cfg
from ..shared.store_base import StoreBase
from .auth import EpicAuthFlow
from .exe_resolver import EpicExeResolver
from .install import EpicInstaller, ProgressCallback
from .library import EpicLibraryReader, merge_install_status
from .updates import EpicUpdateChecker

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.event_bus import EventBus
    from ...services.shortcut.service import ShortcutService

logger = logging.getLogger(__name__)
_LEGENDARY_USER_JSON = '~/.config/legendary/user.json'


class EpicStore(StoreBase):
    """Epic store."""

    store_info = StoreInfo(
        name='epic',
        display_name='Epic Games',
        auth_method='oauth',
        icon_asset='epic.png',
        uses_wine=False,
        supports_install=True,
    )
    CLI_TOOL = CLITool(
        name='legendary',
        search_paths=['bin/legendary'],
        version_flag='--version',
    )

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        plugin_dir: str | None = None,
        config: ConfigManager | None = None,
        browser_monitor: OAuthBrowserMonitor | None = None,
        shortcut_service: ShortcutService | None = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(bus, cache, plugin_dir, config)
        self._config_manager = config
        self._shortcut_service = shortcut_service
        self._browser_monitor = browser_monitor
        self._cli_path = self._find_binary(self.CLI_TOOL)
        epic_cfg = self._read_epic_config(config)
        self._build_cli_submodules(bus, epic_cfg)
        self._build_auth_submodule(bus, browser_monitor)
        logger.info(
            '[EpicStore] cli=%s default_install=%s',
            self._cli_path, epic_cfg.get('install_root'),
        )

    def _read_epic_config(
        self, config: ConfigManager | None,
    ) -> dict[str, Any]:
        """Read epic config."""
        cli_timeouts = read_cli_timeouts(config) if config else {}
        return {
            'install_root': str(get_cfg(
                config, 'stores.epic.install_root', '~/Games/Epic',
            )),
            'library_timeout': int(cli_timeouts.get('library_fetch', 30)),
            'installed_ttl': int(get_cfg(
                config, 'stores.epic.installed_ttl_seconds', 30,
            )),
            'install_timeout': int(get_cfg(
                config, 'stores.epic.install_timeout_seconds', 7200,
            )),
            'uninstall_timeout': int(get_cfg(
                config, 'stores.epic.uninstall_timeout_seconds', 120,
            )),
            'updates_list_timeout': int(cli_timeouts.get('install_poll', 30)),
            'size_cache_ttl': int(get_cfg(
                config, 'stores.epic.size_cache_ttl_seconds', 300,
            )),
            'info_timeout': float(cli_timeouts.get('version_check', 30)),
            'auth_url_timeout': int(cli_timeouts.get('auth_check', 30)),
        }

    def _build_cli_submodules(
        self, bus: EventBus, epic_cfg: dict[str, Any],
    ) -> None:
        """Build CLI submodules."""
        self._library = EpicLibraryReader(
            cli_path=self._cli_path,
            library_timeout=epic_cfg['library_timeout'],
            installed_ttl=epic_cfg['installed_ttl'],
        )
        self._exe_resolver = EpicExeResolver(
            cli_path=self._cli_path,
            find_exe=self._find_exe,
            info_timeout_seconds=epic_cfg['info_timeout'],
        )
        self._installer = EpicInstaller(
            bus=bus,
            cli_path=self._cli_path,
            library=self._library,
            exe_resolver=self._exe_resolver,
            default_install_root=str(epic_cfg['install_root']),
            install_timeout_seconds=epic_cfg['install_timeout'],
            uninstall_timeout_seconds=epic_cfg['uninstall_timeout'],
        )
        self._updates = EpicUpdateChecker(
            bus=bus,
            cli_path=self._cli_path,
            library=self._library,
            list_updates_timeout=epic_cfg['updates_list_timeout'],
            size_cache_ttl=epic_cfg['size_cache_ttl'],
            info_timeout=epic_cfg['info_timeout'],
        )
        self._auth_url_timeout = epic_cfg['auth_url_timeout']

    def _build_auth_submodule(
        self,
        bus: EventBus,
        browser_monitor: OAuthBrowserMonitor | None,
    ) -> None:
        """Build auth submodule."""
        if browser_monitor is None:
            self._auth: EpicAuthFlow | None = None
            return
        orchestrator = AuthOrchestrator(
            bus=bus,
            browser_monitor=browser_monitor,
            store_name='epic',
        )
        self._auth = EpicAuthFlow(
            bus=bus,
            orchestrator=orchestrator,
            cli_path=self._cli_path,
            cli_timeout_seconds=self._auth_url_timeout,
        )

    async def is_available(self) -> bool:
        """Is available."""
        if not self._cli_path:
            self._cached_available = False
            return False
        authenticated = self._check_legendary_authenticated()
        self._cached_available = authenticated
        if not authenticated:
            await emit_external_auth_check_failed(
                self._bus, store='epic', reason='no_legendary_user_json',
            )
        return authenticated

    def _check_legendary_authenticated(self) -> bool:
        """Check LEGENDARY authenticated."""
        user_json = os.path.expanduser(_LEGENDARY_USER_JSON)
        if not os.path.isfile(user_json):
            return False
        try:
            with open(user_json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning('[EpicStore] user.json read failed: %s', e)
            return False
        return isinstance(data, dict) and 'access_token' in data

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        if self._auth is None:
            return AuthResult(
                success=False, store='epic', error='auth_not_configured',
            )
        result = await self._auth.start_auth()
        await self._ensure_auth_shortcut()
        return result

    async def complete_auth(
        self, code: str = '', **kwargs: Any,
    ) -> AuthResult:
        """Complete auth."""
        if self._auth is None:
            return AuthResult(
                success=False, store='epic', error='auth_not_configured',
            )
        return await self._auth._register_code(code)

    async def logout(self) -> Result:
        """Logout."""
        if self._auth is None:
            await self._bus.emit(Events.STORE_LOGOUT, store='epic')
            return Result(success=True)
        return await self._auth.logout()

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        try:
            owned = await self._library.read_owned_games()
            installed = await self._library.read_installed_map()
            return merge_install_status(owned, installed)
        except Exception as e:
            logger.warning('[EpicStore] get_library failed: %s', e)
            return None

    async def install_game(
        self,
        game_id: str,
        *,
        base_path: str | None = None,
        progress_cb: ProgressCallback | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Install game."""
        return await self._installer.install_game(
            game_id, base_path=base_path, progress_cb=progress_cb,
        )

    async def uninstall_game(
        self, game_id: str, **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        return await self._installer.uninstall_game(game_id)

    async def update_game(
        self,
        game_id: str,
        progress_cb: ProgressCallback | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Update game."""
        return await self._updates.update_game(
            game_id, installer=self._installer, progress_cb=progress_cb,
        )

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._updates.check_for_updates()

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        return await self._updates.get_game_size(game_id)

    async def _ensure_auth_shortcut(self) -> None:
        """Ensure auth shortcut.

        Epic doesn't need a dedicated Steam auth shortcut — legendary's
        OAuth flow runs entirely through the Edge browser orchestrator
        and never invokes a Wine binary. Kept for PDF-spec parity.
        """
        return


_ = cast
_: Callable[..., Any] | None = None
