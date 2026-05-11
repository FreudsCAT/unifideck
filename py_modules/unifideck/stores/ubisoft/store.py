"""
Ubisoft store — Layer-4 implementation of the unified store interface.

OP-55a | py_modules/unifideck/stores/ubisoft/store.py

``UbisoftStore`` is the orchestration class that wires every sub-component
of the Ubisoft sub-package together and exposes them through the
``StoreBase`` contract used by the rest of the plugin (RPC mixins,
service layer, registry). It owns one instance each of:

* ``UbisoftConfig`` (OP-55b) — frozen configuration snapshot.
* ``UbisoftPrefixPaths`` (OP-55c) — Wine prefix path enumeration helpers.
* ``UbisoftBinaryResolver`` (OP-55d) — UPC binary discovery.
* ``UbisoftAuth`` (OP-58a) — auth flow via Steam shortcut.
* ``UbisoftLibrary`` (OP-57a) — game library facade.
* ``UbisoftInstaller`` (OP-56a) — installer pipeline.
* ``UbisoftPrefixManager`` (OP-59a) — Wine prefix lifecycle.
* ``UbisoftSession`` (OP-60a) — UPC session payload propagation.

The ``_shortcut_service`` attribute is left at ``None`` at construction
time and injected post-discovery by ``services/bootstrap/store_injector.py``;
see the ``_STORE_INJECTIONS`` table for the wiring entry.

Implements the standard ``StoreBase`` API: ``store_info``, ``is_authed``,
``auth``, ``logout``, ``library``, ``install``, ``uninstall``, ``launch``,
etc. — every method is delegated to the appropriate sub-component.
"""

from __future__ import annotations
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast
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
        name="ubisoft",
        display_name="Ubisoft",
        auth_method="shortcut",
        icon_asset="ubisoft.png",
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
        specialists = build_ubisoft_specialists(
            bus=bus,
            config_mgr=config,
            plugin_dir=plugin_dir,
            shortcut_service=shortcut_service,
            steamgriddb=steamgriddb,
        )
        self._config = specialists.config
        self._paths = specialists.paths
        self._binaries = specialists.binaries
        self._id_map = specialists.id_map
        self._session = specialists.session
        self._installer_cache = specialists.installer_cache
        self._prefix_mgr = specialists.prefix_mgr
        self._library: UbisoftLibrary = specialists.library
        self._installer: UbisoftInstaller = specialists.installer
        self._auth: UbisoftAuth = specialists.auth
        self._ubi_config = specialists.config

    async def is_available(self) -> bool:
        """Check whether available."""
        available = await self._auth.is_available()
        self._cached_available = available
        return available

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        await self._auth.ensure_auth_shortcut()
        await self._auth.start_auth_session_monitor()
        return cast("AuthResult", await self._auth.start_auth())

    async def complete_auth(
        self,
        code: str = "",
        **kwargs: Any,
    ) -> AuthResult:
        """Complete auth."""
        return await self._auth.complete_auth(code, **kwargs)

    async def logout(self) -> Result:
        """Logout."""
        return await self._auth.logout()

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        return await self._library.get_library()

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
            game_id,
            progress_cb=progress_cb,
            install_path=install_path,
        )

    async def uninstall_game(
        self,
        game_id: str,
        *,
        delete_prefix: bool = False,
        **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        return await self._installer.uninstall_game(
            game_id,
            delete_prefix=delete_prefix,
        )

    async def update_game(
        self,
        game_id: str,
        **kwargs: Any,
    ) -> InstallResult:
        """Update game."""
        return await self._installer.update_game(game_id)

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._installer.check_for_updates()

    async def get_game_size(
        self,
        game_id: str,
    ) -> int | None:
        """Get game size."""
        return None

    async def get_installed(self) -> dict[str, Any]:
        """Get installed."""
        return await self._library.get_installed()

    def get_installed_game_info(
        self,
        game_id: str,
    ) -> dict[str, Any] | None:
        """Get installed game info."""
        return self._library.get_installed_game_info(game_id)

    async def write_install_marker(
        self,
        space_id: str,
        install_path: str,
        executable: str,
        game_title: str = "",
    ) -> None:
        """Write install marker."""
        await self._library.write_install_marker(
            space_id=space_id,
            install_path=install_path,
            executable=executable,
            game_title=game_title,
        )

    def find_game_executable(
        self,
        install_path: str,
    ) -> str | None:
        """Find game executable."""
        return self._library.find_game_executable(install_path)

    def is_install_session_active(self, game_id: str) -> bool:
        """Check whether install session active."""
        return self._installer.is_install_session_active(game_id)

    async def cancel_install_session(
        self,
        game_id: str,
    ) -> Result:
        """Check whether install session."""
        return await self._installer.cancel_install_session(
            game_id,
        )

    async def open_launcher_for_install(
        self,
        game_id: str,
    ) -> Result:
        """Open launcher for install."""
        return await self._installer.open_launcher_for_install(
            game_id,
        )

    def resolve_install_id(
        self,
        space_id: str,
    ) -> str | None:
        """Resolve install ID."""
        return self._id_map.resolve_install_id(space_id)

    def resolve_launch_id(
        self,
        space_id: str,
    ) -> str | None:
        """Resolve launch ID."""
        return self._id_map.resolve_launch_id(space_id)

    async def get_auth_shortcut_context(
        self,
    ) -> dict[str, Any]:
        """Get auth shortcut context."""
        return await self._auth.get_auth_shortcut_context()

    async def start_auth_session_monitor(self) -> Result:
        """Start auth session monitor."""
        return await self._auth.start_auth_session_monitor()

    def check_auth_session_status(self) -> dict[str, Any]:
        """Check auth session status."""
        return self._auth.check_auth_session_status()

    async def connect_ubisoft_account(
        self,
    ) -> dict[str, Any]:
        """Connect UBISOFT account."""
        return await self._auth.connect_ubisoft_account()

    def sync_ubisoft_credentials(self) -> dict[str, Any]:
        """Sync UBISOFT credentials."""
        return self._session.retroactive_sync()

    async def repair_prefix(self, space_id: str) -> Result:
        """Repair prefix."""
        success = await self._prefix_mgr.repair_prefix(space_id)
        if not success:
            return Result(
                success=False,
                error="prefix_repair_failed",
            )
        prefix_path = self._paths.get_prefix_path(space_id)
        self._session.inject_into_prefix(prefix_path)
        install_id = self._id_map.resolve_install_id(space_id)
        if install_id:
            game_info = self._library._detector._detect_installed_game(
                space_id,
                prefix_path,
            )
            if game_info and game_info.get("install_path"):
                self._installer.inject_install_registry(
                    prefix_path,
                    install_id,
                    game_info["install_path"],
                )
        return Result(success=True)

    def get_game_official_url(
        self,
        game_id: str,
    ) -> str | None:
        """Get game official URL."""
        return self._library.get_game_official_url(game_id)

    def kill_upc_processes(self) -> None:
        """Kill UPC processes."""
        self._installer.kill_upc_processes()
