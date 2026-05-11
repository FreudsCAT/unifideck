"""amazon_store.py — Public ``AmazonStore`` (StoreBase implementation).

# OP-49a | py_modules/unifideck/stores/amazon/amazon_store.py | Depends: OP-47b

Façade that wires nile, the library reader, install/update pipelines
and the OAuth auth flow into a single :class:`StoreBase` subclass.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
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
from .amazon_auth import AmazonAuthFlow
from .amazon_install import AmazonInstaller, ProgressCallback
from .amazon_library import AmazonLibraryReader, merge_install_status
from .amazon_updates import AmazonUpdateChecker

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.event_bus import EventBus
    from ...services.shortcut.service import ShortcutService

logger = logging.getLogger(__name__)
_NILE_USER_JSON = '~/.config/nile/user.json'
_NILE_CONFIG_DIR = '~/.config/nile'
_DEFAULT_SUCCESS_MARKERS: list[str] = [
    'maplanding', 'access_token', 'refresh_token',
]


class AmazonStore(StoreBase):
    """Amazon store."""

    store_info = StoreInfo(
        name='amazon',
        display_name='Amazon Games',
        auth_method='oauth',
        icon_asset='amazon.png',
        uses_wine=False,
        supports_install=True,
    )
    CLI_TOOL = CLITool(
        name='nile',
        search_paths=['bin/nile'],
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
        amazon_cfg = self._read_amazon_config(config)
        self._library = AmazonLibraryReader(
            config_dir=str(amazon_cfg['nile_config_dir']),
        )
        self._installer = AmazonInstaller(
            bus=bus,
            cli_path=self._cli_path,
            library=self._library,
            find_exe=self._find_exe,
            default_install_root=str(amazon_cfg['install_root']),
            install_timeout_seconds=int(amazon_cfg['install_timeout']),
            uninstall_timeout_seconds=int(amazon_cfg['uninstall_timeout']),
        )
        self._updates = AmazonUpdateChecker(
            bus=bus,
            cli_path=self._cli_path,
            library=self._library,
            list_updates_timeout=int(amazon_cfg['updates_list_timeout']),
            get_size_timeout=int(amazon_cfg['info_timeout']),
            default_install_root=str(amazon_cfg['install_root']),
        )
        self._auth_url_timeout = int(amazon_cfg['auth_url_timeout'])
        self._success_markers = list(amazon_cfg['success_markers'])
        if browser_monitor is not None:
            orchestrator = AuthOrchestrator(
                bus=bus,
                browser_monitor=browser_monitor,
                store_name='amazon',
            )
            self._auth: AmazonAuthFlow | None = AmazonAuthFlow(
                bus=bus,
                orchestrator=orchestrator,
                cli_path=self._cli_path,
                success_markers=self._success_markers,
                cli_timeout_seconds=self._auth_url_timeout,
            )
        else:
            self._auth = None
        logger.info(
            '[AmazonStore] cli=%s install_root=%s',
            self._cli_path, amazon_cfg['install_root'],
        )

    def _read_amazon_config(
        self, config: ConfigManager | None,
    ) -> dict[str, Any]:
        """Read amazon config."""
        cli_timeouts = read_cli_timeouts(config) if config else {}
        return {
            'install_root': str(get_cfg(
                config, 'stores.amazon.install_root', '~/Games/Amazon',
            )),
            'nile_config_dir': str(get_cfg(
                config, 'stores.amazon.nile_config_dir', _NILE_CONFIG_DIR,
            )),
            'install_timeout': int(get_cfg(
                config, 'stores.amazon.install_timeout_seconds', 3600,
            )),
            'uninstall_timeout': int(get_cfg(
                config, 'stores.amazon.uninstall_timeout_seconds', 120,
            )),
            'updates_list_timeout': int(cli_timeouts.get('install_poll', 30)),
            'info_timeout': int(cli_timeouts.get('version_check', 30)),
            'auth_url_timeout': int(cli_timeouts.get('auth_check', 30)),
            'success_markers': _DEFAULT_SUCCESS_MARKERS,
        }

    async def is_available(self) -> bool:
        """Is available."""
        if not self._cli_path:
            self._cached_available = False
            return False
        authenticated = self._check_nile_authenticated()
        self._cached_available = authenticated
        if not authenticated:
            await emit_external_auth_check_failed(
                self._bus, store='amazon', reason='no_nile_user_json',
            )
        return authenticated

    def _check_nile_authenticated(self) -> bool:
        """Check NILE authenticated."""
        user_json = os.path.expanduser(_NILE_USER_JSON)
        if not os.path.isfile(user_json):
            return False
        try:
            with open(user_json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning('[AmazonStore] user.json read: %s', e)
            return False
        if not isinstance(data, dict):
            return False
        extensions = data.get('extensions')
        return isinstance(extensions, dict) and 'customer_info' in extensions

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        if self._auth is None:
            return AuthResult(
                success=False, store='amazon', error='auth_not_configured',
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
                success=False, store='amazon', error='auth_not_configured',
            )
        return await self._auth._register_code(code)

    async def logout(self) -> Result:
        """Logout."""
        if self._auth is None:
            await self._bus.emit(Events.STORE_LOGOUT, store='amazon')
            return Result(success=True)
        return await self._auth.logout()

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        try:
            owned = await self._library.read_owned_games()
            installed = await self._library.read_installed_ids()
            return merge_install_status(owned, installed)
        except Exception as e:
            logger.warning('[AmazonStore] get_library failed: %s', e)
            return None

    async def install_game(
        self,
        game_id: str,
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
        """Update game.

        Amazon's ``nile`` doesn't ship a distinct ``update`` verb —
        ``install`` is idempotent and applies any pending content
        update. We delegate to install_game so the same progress
        callback pipeline runs.
        """
        base_path = await self._updates.resolve_current_base_path(game_id)
        return await self._installer.install_game(
            game_id, base_path=base_path, progress_cb=progress_cb,
        )

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._updates.check_for_updates()

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        return await self._updates.get_game_size(game_id)

    async def get_official_url(self, game_id: str) -> str | None:
        """Get official URL."""
        return await self._library.get_official_url(game_id)

    async def _ensure_auth_shortcut(self) -> None:
        """Ensure auth shortcut.

        Amazon's OAuth runs through the Edge browser orchestrator
        (no Wine binary involved), so no Steam auth shortcut is
        required. Kept for PDF-spec parity.
        """
        return


_: Callable[..., Any] | None = None
_ = cast
_ = Path
_ = Awaitable
