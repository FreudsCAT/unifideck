"""store.py — Public ``UbisoftStore`` (StoreBase implementation).

# OP-55a | py_modules/unifideck/stores/ubisoft/store.py | Depends: (none)

Thin façade over :class:`UbisoftSpecialists` — implements every
``StoreBase`` abstract method by delegating to one of the helpers.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ...core.types import (
    AuthResult,
    Game,
    InstallResult,
    Result,
    StoreInfo,
)
from ..shared.store_base import StoreBase
from .specialists import build_ubisoft_specialists

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.event_bus import EventBus
    from ...services.shortcut import ShortcutService
    from ...steam.steamgriddb import SteamGridDBClient
    from .auth import UbisoftAuth
    from .installer import UbisoftInstaller
    from .library import UbisoftLibrary

logger = logging.getLogger(__name__)


class UbisoftStore(StoreBase):
    """Ubisoft store."""

    store_info = StoreInfo(
        name='ubisoft',
        display_name='Ubisoft',
        auth_method='shortcut',
        icon_asset='ubisoft.png',
        uses_wine=True,
        supports_install=True,
    )

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        plugin_dir: str | None = None,
        config: ConfigManager | None = None,
        shortcut_service: ShortcutService | None = None,
        steamgriddb: SteamGridDBClient | None = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(bus, cache, plugin_dir, config)
        self._specialists = build_ubisoft_specialists(
            bus=bus, config_mgr=config, plugin_dir=plugin_dir,
            shortcut_service=shortcut_service,
            steamgriddb=steamgriddb,
        )
        logger.info(
            '[UbisoftStore] %s', self._specialists.config.describe(),
        )

    @property
    def _auth(self) -> UbisoftAuth:
        """Auth shortcut."""
        return self._specialists.auth

    @property
    def _library(self) -> UbisoftLibrary:
        """Library shortcut."""
        return self._specialists.library

    @property
    def _installer(self) -> UbisoftInstaller:
        """Installer shortcut."""
        return self._specialists.installer

    async def is_available(self) -> bool:
        """Is available."""
        result = await self._auth.is_available()
        self._cached_available = result
        return result

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        return await self._auth.start_auth()

    async def complete_auth(self, code: str = '', **kwargs: Any) -> AuthResult:
        """Complete auth."""
        return await self._auth.complete_auth(code, **kwargs)

    async def logout(self) -> Result:
        """Logout."""
        return await self._auth.logout()

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        try:
            return await self._library.get_library()
        except Exception as e:
            logger.warning('[UbisoftStore] get_library failed: %s', e)
            return None

    async def install_game(
        self,
        game_id: str,
        *,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        install_path: str | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Install game."""
        return await self._installer.install_game(
            game_id, progress_cb=progress_cb, install_path=install_path,
        )

    async def uninstall_game(
        self, game_id: str, *, delete_prefix: bool = False,
        **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        return await self._installer.uninstall_game(
            game_id, delete_prefix=delete_prefix,
        )

    async def update_game(
        self, game_id: str, **kwargs: Any,
    ) -> InstallResult:
        """Update game."""
        return await self._installer.update_game(game_id)

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._installer.check_for_updates()

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        info = self.get_installed_game_info(game_id)
        if not info:
            return None
        path = info.get('install_path')
        if not path:
            return None
        from .installer.registry import get_directory_size
        return get_directory_size(path)

    async def get_installed(self) -> dict[str, Any]:
        """Get installed."""
        return await self._library.get_installed()

    def get_installed_game_info(self, game_id: str) -> dict[str, Any] | None:
        """Get installed game info."""
        return self._library.get_installed_game_info(game_id)

    async def write_install_marker(
        self, space_id: str, install_path: str, executable: str,
        game_title: str = '',
    ) -> None:
        """Write install marker."""
        await self._library.write_install_marker(
            space_id, install_path, executable, game_title,
        )

    def find_game_executable(self, install_path: str) -> str | None:
        """Find game executable."""
        return self._library.find_game_executable(install_path)

    def is_install_session_active(self, game_id: str) -> bool:
        """Is install session active."""
        return self._installer.is_install_session_active(game_id)

    async def cancel_install_session(self, game_id: str) -> Result:
        """Cancel install session."""
        return await self._installer.cancel_install_session(game_id)

    async def open_launcher_for_install(self, game_id: str) -> Result:
        """Open launcher for install."""
        return await self._installer.open_launcher_for_install(game_id)

    def resolve_install_id(self, space_id: str) -> str | None:
        """Resolve install ID."""
        return self._specialists.id_map.resolve_install_id(space_id)

    def resolve_launch_id(self, space_id: str) -> str | None:
        """Resolve launch ID."""
        return self._specialists.id_map.resolve_launch_id(space_id)

    async def get_auth_shortcut_context(self) -> dict[str, Any]:
        """Get auth shortcut context."""
        return await self._auth.get_auth_shortcut_context()

    async def start_auth_session_monitor(self) -> Result:
        """Start auth session monitor."""
        return await self._auth.start_auth_session_monitor()

    def check_auth_session_status(self) -> dict[str, Any]:
        """Check auth session status."""
        return self._auth.check_auth_session_status()

    async def connect_ubisoft_account(self) -> dict[str, Any]:
        """Connect UBISOFT account."""
        return await self._auth.connect_ubisoft_account()

    def sync_ubisoft_credentials(self) -> dict[str, Any]:
        """Sync UBISOFT credentials."""
        return self._specialists.session.retroactive_sync()

    async def repair_prefix(self, space_id: str) -> Result:
        """Repair prefix."""
        ok = await self._specialists.prefix_mgr.repair_prefix(space_id)
        return Result(success=ok)

    def get_game_official_url(self, game_id: str) -> str | None:
        """Get game official URL."""
        return self._library.get_game_official_url(game_id)

    def kill_upc_processes(self) -> None:
        """Kill UPC processes."""
        self._installer.kill_upc_processes()
