"""store.py — Public ``GOGStore`` (StoreBase implementation).

# OP-50a | py_modules/unifideck/stores/gog/store.py | Depends: (none)

Façade that wires all the GOG submodules behind a single
:class:`StoreBase` subclass.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ...auth.browser import OAuthBrowserMonitor
from ...auth.edge_browser import EdgeBrowser
from ...auth.orchestrator import AuthOrchestrator
from ...core.types import (
    AuthResult,
    Events,
    Game,
    InstallResult,
    Result,
    StoreInfo,
)
from ...utils.locale import get_unifideck_locale
from ..shared.store_base import StoreBase
from .auth import GOGBrowserAuth
from .config import GOG_AUTH_URL_FILE, GOGConfig
from .dlc import GOGDlcManager
from .exe_resolver import GOGExeResolver
from .install import GOGInstaller
from .library import GOGLibrary
from .tokens import GOGTokenManager
from .updates import GOGUpdatesChecker

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.event_bus import EventBus
    from ...services.shortcut.service import ShortcutService

logger = logging.getLogger(__name__)


class GOGStore(StoreBase):
    """GOG store."""

    store_info = StoreInfo(
        name='gog',
        display_name='GOG',
        auth_method='oauth',
        icon_asset='gog.png',
        uses_wine=False,
        supports_install=True,
    )

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        plugin_dir: str | None = None,
        config: ConfigManager | None = None,
        browser_monitor: OAuthBrowserMonitor | None = None,
        shortcut_service: ShortcutService | None = None,
        edge_browser: EdgeBrowser | None = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(bus, cache, plugin_dir, config)
        self._gog_config = GOGConfig.from_config_manager(config)
        logger.info('[GOGStore] %s', self._gog_config.describe())
        self._config_manager = config
        self._shortcut_service = shortcut_service
        self._edge = edge_browser
        self._browser_monitor = browser_monitor
        self._tokens = GOGTokenManager(
            config=self._gog_config, bus=bus,
        )
        self._exe_resolver = GOGExeResolver()
        self._library = GOGLibrary(
            config=self._gog_config,
            tokens=self._tokens,
            exe_finder=self._exe_resolver.find,
        )
        gogdl_bin = self._resolve_gogdl_bin()
        self._installer = GOGInstaller(
            config=self._gog_config,
            tokens=self._tokens,
            gogdl_bin=gogdl_bin or '',
            exe_finder=self._exe_resolver.find,
            locale_fn=self._unifideck_locale,
        )
        self._dlc = GOGDlcManager(
            config=self._gog_config,
            tokens=self._tokens,
            gogdl_bin=gogdl_bin or '',
            locale_fn=self._unifideck_locale,
            resolve_install_path=self._library.get_installed_game_info,
        )
        self._updates = GOGUpdatesChecker(
            config=self._gog_config,
            tokens=self._tokens,
            gogdl_bin=gogdl_bin or '',
            get_installed_ids=self._library.get_installed,
            resolve_install_info=self._library.get_installed_game_info,
        )
        if browser_monitor is not None:
            orchestrator = AuthOrchestrator(
                bus=bus,
                browser_monitor=browser_monitor,
                store_name='gog',
            )
            self._auth: GOGBrowserAuth | None = GOGBrowserAuth(
                bus=bus,
                orchestrator=orchestrator,
                tokens=self._tokens,
                config=self._gog_config,
            )
        else:
            self._auth = None

    def _unifideck_locale(self) -> str:
        """Unifideck locale."""
        try:
            return get_unifideck_locale(self._config_manager) or 'en-US'
        except Exception:
            return 'en-US'

    async def is_available(self) -> bool:
        """Is available."""
        if not self._gog_config.is_valid():
            self._cached_available = False
            return False
        if not await self._tokens.load():
            self._cached_available = False
            return False
        result = await self._library.is_available()
        self._cached_available = result
        return result

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        if self._auth is None:
            return AuthResult(
                success=False, store='gog', error='auth_not_configured',
            )
        return await self._auth.start_auth()

    async def complete_auth(
        self, code: str = '', **kwargs: Any,
    ) -> AuthResult:
        """Complete auth."""
        if self._auth is None:
            return AuthResult(
                success=False, store='gog', error='auth_not_configured',
            )
        if not code:
            return AuthResult(
                success=False, store='gog', error='no_auth_code',
            )
        ok = await self._tokens.exchange_code(code)
        if not ok:
            await self._bus.emit(
                Events.STORE_AUTH_FAILED, store='gog', error='exchange_failed',
            )
            return AuthResult(
                success=False, store='gog', error='exchange_failed',
            )
        await self._ensure_auth_shortcut()
        await self._bus.emit(Events.STORE_AUTH_SUCCESS, store='gog')
        return AuthResult(success=True, store='gog')

    async def logout(self) -> Result:
        """Logout."""
        await self._tokens.clear()
        if self._auth is not None:
            return await self._auth.logout(
                browser_monitor=self._browser_monitor,
            )
        await self._bus.emit(Events.STORE_LOGOUT, store='gog')
        return Result(success=True)

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        try:
            return await self._library.fetch_library()
        except Exception as e:
            logger.warning('[GOGStore] get_library failed: %s', e)
            return None

    async def install_game(
        self,
        game_id: str,
        *,
        base_path: str | None = None,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        language: str | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Install game."""
        return await self._installer.install_game(
            game_id,
            base_path=base_path,
            progress_cb=progress_cb,
            language=language,
        )

    async def uninstall_game(
        self, game_id: str, **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        info = self._library.get_installed_game_info(game_id) or {}
        install_path = info.get('install_path')
        return await self._installer.uninstall_game(
            game_id, install_path=install_path,
        )

    async def update_game(
        self, game_id: str, **kwargs: Any,
    ) -> InstallResult:
        """Update game."""
        info = self._library.get_installed_game_info(game_id) or {}
        install_path = info.get('install_path')
        result = await self._updates.update_game(
            game_id, install_path=install_path,
        )
        if result.success:
            return InstallResult(
                success=True, store='gog', game_id=game_id,
                install_path=install_path or '',
            )
        return InstallResult(
            success=False, store='gog', game_id=game_id,
            error=str(result.error or 'update_failed'),
        )

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._updates.check_for_updates()

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        info = self._library.get_installed_game_info(game_id) or {}
        path = info.get('install_path')
        if not path or not os.path.isdir(path):
            return None
        from .install.primitives import GOGFolderOps
        return GOGFolderOps.folder_size(path)

    async def get_game_dlcs(self, game_id: str) -> list[dict[str, Any]]:
        """Get game DLCs."""
        return await self._dlc.get_game_dlcs(game_id)

    async def get_available_languages(self, game_id: str) -> list[str]:
        """Get available languages."""
        return await self._dlc.get_available_languages(game_id)

    async def install_dlc(
        self,
        game_id: str,
        dlc_id: str,
        base_path: str | None = None,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> Result:
        """Install DLC."""
        return await self._dlc.install_dlc(
            game_id, dlc_id, base_path=base_path, progress_cb=progress_cb,
        )

    async def get_game_store_url(self, game_id: str) -> str | None:
        """Get game store URL."""
        return await self._dlc.get_game_store_url(game_id)

    async def get_game_slug(self, game_id: str) -> str | None:
        """Get game slug."""
        return await self._library.get_game_slug(game_id)

    def get_installed(self) -> list[str]:
        """Get installed."""
        return self._library.get_installed()

    def get_installed_game_info(
        self, game_id: str,
    ) -> dict[str, str | None] | None:
        """Get installed game info."""
        return self._library.get_installed_game_info(game_id)

    def migrate_old_markers(self) -> dict[str, int]:
        """Migrate old markers."""
        return self._library.migrate_old_markers()

    def _resolve_gogdl_bin(self) -> str:
        """Resolve GOGDL bin."""
        if self._plugin_dir:
            candidate = os.path.join(self._plugin_dir, 'bin', 'gogdl', 'gogdl')
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        for candidate in (
            os.path.expanduser('~/.local/bin/gogdl'),
            '/usr/bin/gogdl',
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return ''

    async def _ensure_auth_shortcut(self) -> None:
        """Ensure auth shortcut."""
        # GOG doesn't need a Steam auth shortcut — UPC-only concern.

    def _browser_monitor_from_auth(self) -> OAuthBrowserMonitor | None:
        """Browser monitor from auth."""
        return self._browser_monitor


_ = GOG_AUTH_URL_FILE
