"""Epic Games Store — Layer-4 implementation of the unified store interface.

OP-48a | py_modules/unifideck/stores/epic/store.py

``EpicStore`` is the orchestration class that wires every Epic
sub-component together and exposes them through the ``StoreBase``
contract. It owns one instance each of :

* ``EpicAuthFlow`` (OP-48b)      — OAuth via embedded browser.
* ``EpicLibraryReader`` (OP-48c) — owned-games library reader.
* ``EpicInstaller`` (OP-48d)     — install/uninstall pipeline.
* ``EpicUpdateChecker`` (OP-48e) — periodic update polling.
* ``EpicExeResolver`` (OP-48g)   — locate the launchable .exe.

Epic Games uses ``legendary`` (a community CLI replacement for the
Epic Games Launcher, written in Python) for all download/install
operations. The store class is the high-level coordinator that
orchestrates token lifecycle, library fetch, install pipeline,
update detection, and post-install exe resolution.

Implements the standard ``StoreBase`` API : ``store_info``,
``is_authed``, ``auth``, ``logout``, ``library``, ``install``,
``uninstall``, ``launch``, etc. — each method delegates to the
appropriate sub-component.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, cast
from ...auth.browser import OAuthBrowserMonitor
from ...auth.orchestrator import AuthOrchestrator
from ...core.bin import read_cli_timeouts
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
from ...services.shortcut import ShortcutService
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

logger = logging.getLogger(__name__)


class EpicStore(StoreBase):
    """Epic store."""

    store_info = StoreInfo(
        name="epic",
        display_name="Epic Games",
        auth_method="oauth",
        icon_asset="epic.png",
        uses_wine=False,
        supports_install=True,
    )

    CLI_TOOL = CLITool(
        name="legendary",
        search_paths=["bin/legendary"],
        version_flag="--version",
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
        self.cli_path: str | None = self._find_binary(self.CLI_TOOL)
        if not self.cli_path:
            logger.warning("[EpicStore] legendary binary not found")
        self._shortcut_service = shortcut_service
        self._timeouts = read_cli_timeouts(config)
        epic_cfg = config.get("stores.epic") if config else None
        if epic_cfg is None:
            raise KeyError("config.stores.epic is required")
        self._build_cli_submodules(bus, epic_cfg)
        self._build_auth_submodule(bus, browser_monitor)

    def _build_cli_submodules(self, bus: EventBus, epic_cfg: dict[str, Any]) -> None:
        """Build cli submodules."""
        self._library = EpicLibraryReader(
            cli_path=self.cli_path,
            library_timeout=self._timeouts["library_fetch"],
        )
        self._exe_resolver = EpicExeResolver(
            cli_path=self.cli_path,
            find_exe=self._find_exe,
            info_timeout_seconds=epic_cfg["info_timeout_seconds"],
        )
        self._installer = EpicInstaller(
            bus=bus,
            cli_path=self.cli_path,
            library=self._library,
            exe_resolver=self._exe_resolver,
            default_install_root=epic_cfg["default_install_root"],
        )
        self._updates = EpicUpdateChecker(
            bus=bus,
            cli_path=self.cli_path,
            library=self._library,
            list_updates_timeout=epic_cfg["list_updates_timeout_seconds"],
            size_cache_ttl=epic_cfg["size_cache_ttl_seconds"],
            info_timeout=epic_cfg["info_timeout_seconds"],
        )

    def _build_auth_submodule(self, bus: EventBus, browser_monitor: OAuthBrowserMonitor | None) -> None:
        """Build auth submodule."""
        if browser_monitor is None:
            self._auth: EpicAuthFlow | None = None
            logger.debug("[EpicStore] no browser_monitor; auth disabled")
            return
        orchestrator = AuthOrchestrator(
            bus=bus,
            browser_monitor=browser_monitor,
            store_name="epic",
        )
        self._auth = EpicAuthFlow(
            bus=bus,
            orchestrator=orchestrator,
            cli_path=self.cli_path,
            cli_timeout_seconds=self._timeouts["auth_check"],
        )

    async def is_available(self) -> bool:
        """Check whether available."""
        ok = self._check_legendary_authenticated()
        self._cached_available = ok
        return ok

    def _check_legendary_authenticated(self) -> bool:
        """Check LEGENDARY authenticated."""
        if not self.cli_path:
            emit_external_auth_check_failed(
                self._bus,
                "epic",
                "cli_not_found",
                "legendary binary missing from search paths",
            )
            return False
        user_file = os.path.expanduser(
            get_cfg(
                self._config,
                "stores.epic.user_file",
                "~/.config/legendary/user.json",
            ),
        )
        if not os.path.isfile(user_file):
            return False
        try:
            with open(user_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("[EpicStore] user.json invalid: %s", e)
            emit_external_auth_check_failed(
                self._bus,
                "epic",
                "parse_error",
                f"{type(e).__name__}",
            )
            return False
        if not isinstance(data, dict):
            emit_external_auth_check_failed(
                self._bus,
                "epic",
                "malformed_payload",
                "not a JSON object",
            )
            return False
        return "access_token" in data

    async def start_auth(self, **kwargs) -> AuthResult:
        """Start auth."""
        if self._auth is None:
            return AuthResult(
                success=False,
                error="auth_not_configured",
                store="epic",
            )
        await self._ensure_auth_shortcut()
        return cast("AuthResult", await self._auth.start_auth())

    async def complete_auth(self, code: str = "", **kwargs) -> AuthResult:
        """Complete auth."""
        if await self.is_available():
            return AuthResult(success=True, store="epic")
        return AuthResult(
            success=False,
            error="not_authenticated",
            store="epic",
        )

    async def logout(self) -> Result:
        """Logout."""
        if self._auth is None:
            await self._emit(Events.STORE_LOGOUT, store="epic")
            return Result(success=True)
        return await self._auth.logout()

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        if not self.cli_path:
            return []
        try:
            owned = await self._library.read_owned_games()
            installed = await self._library.read_installed_map()
            return merge_install_status(owned, installed)
        except Exception as e:
            logger.error("[EpicStore] get_library failed: %s", e)
            return []

    async def install_game(self, game_id: str, base_path: str | None = None
                           progress_cb: ProgressCallback | None = None, **kwargs: Any) -> InstallResult:
        """Install game."""
        return await self._installer.install_game(
            game_id,
            base_path,
            progress_cb,
        )

    async def uninstall_game(self, game_id: str, **kwargs: Any) -> Result:
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
            game_id,
            installer=self._installer,
            progress_cb=progress_cb,
        )

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._updates.check_for_updates()

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        return await self._updates.get_game_size(game_id)

    async def _ensure_auth_shortcut(self) -> None:
        """Ensure auth shortcut."""
        if self._shortcut_service is None:
            logger.debug("[EpicStore] no shortcut_service injected; skipping auth shortcut creation")
            return
        launcher = os.path.join(
            self._plugin_dir or "",
            "py_modules",
            "unifideck",
            "launcher",
            "dispatcher.py",
        )
        if not os.path.isfile(launcher):
            logger.warning("[EpicStore] launcher dispatcher not found at %s", launcher)
            return
        result = await self._shortcut_service.add_auth_shortcut(
            store="epic",
            launcher_path=launcher,
            title="Epic Games Sign-In",
        )
        if not result.success:
            logger.warning("[EpicStore] add_auth_shortcut failed: %s", result.error)
