"""Amazon Games store — Layer-4 implementation of the unified store interface.

OP-49a | py_modules/unifideck/stores/amazon/amazon_store.py

``AmazonStore`` is the orchestration class that wires every Amazon
sub-component together and exposes them through the ``StoreBase``
contract. It owns one instance each of :

* ``AmazonAuthFlow`` (OP-49b)      — embedded-browser OAuth flow.
* ``AmazonLibraryReader`` (OP-49c) — owned-games library reader.
* ``AmazonInstaller`` (OP-49d)     — install/uninstall pipeline.
* ``AmazonUpdateChecker`` (OP-49e) — periodic update polling.

Amazon Games uses ``nile`` (a community CLI mirror of the Amazon
Games launcher) for the actual downloads ; the store class is the
high-level coordinator that orchestrates token lifecycle, library
fetch, install pipeline, and update detection.

Implements the standard ``StoreBase`` API : ``store_info``,
``is_authed``, ``auth``, ``logout``, ``library``, ``install``,
``uninstall``, ``launch``, etc. — each method delegates to the
appropriate sub-component.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from unifideck.auth.browser import OAuthBrowserMonitor
from unifideck.auth.orchestrator import AuthOrchestrator
from unifideck.core.types import AuthResult, CLITool, Events, Game, InstallResult, Result, StoreInfo
from unifideck.security import emit_external_auth_check_failed
from unifideck.services.shortcut import ShortcutService
from unifideck.stores.shared.store_base import StoreBase
from unifideck.utils.config_helpers import get_cfg

from .amazon_auth import AmazonAuthFlow
from .amazon_install import AmazonInstaller, ProgressCallback
from .amazon_library import AmazonLibraryReader, merge_install_status
from .amazon_updates import AmazonUpdateChecker

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus.event_bus import EventBus
logger = logging.getLogger(__name__)


class AmazonStore(StoreBase):
    """Amazon store."""

    store_info = StoreInfo(
        name="amazon",
        display_name="Amazon Games",
        auth_method="oauth",
        icon_asset="amazon.png",
        uses_wine=False,
        supports_install=True,
    )
    CLI_TOOL = CLITool(
        name="nile",
        search_paths=["bin/nile"],
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
            logger.warning("[AmazonStore] nile binary not found")
        self._shortcut_service = shortcut_service
        amazon_cfg = config.get("stores.amazon") if config else None
        if amazon_cfg is None:
            raise KeyError(
                "config.stores.amazon is required",
            )
        self._library = AmazonLibraryReader(
            config_dir=amazon_cfg["nile_config_dir"],
        )
        self._installer = AmazonInstaller(
            bus=bus,
            cli_path=self.cli_path,
            library=self._library,
            find_exe=self._find_exe,
            default_install_root=amazon_cfg["default_install_root"],
        )
        self._updates = AmazonUpdateChecker(
            bus=bus,
            cli_path=self.cli_path,
            library=self._library,
            list_updates_timeout=amazon_cfg["list_updates_timeout_seconds"],
            get_size_timeout=amazon_cfg["get_size_timeout_seconds"],
            default_install_root=amazon_cfg["default_install_root"],
        )
        if browser_monitor is not None:
            orchestrator = AuthOrchestrator(
                bus=bus,
                browser_monitor=browser_monitor,
                store_name="amazon",
            )
            self._auth: AmazonAuthFlow | None = AmazonAuthFlow(
                bus=bus,
                orchestrator=orchestrator,
                cli_path=self.cli_path,
                success_markers=amazon_cfg["nile_register_success_markers"],
            )
        else:
            self._auth = None
            logger.debug(
                "[AmazonStore] no browser_monitor; auth disabled",
            )

    async def is_available(self) -> bool:
        """Check whether available."""
        ok = self._check_nile_authenticated()
        self._cached_available = ok
        return ok

    def _check_nile_authenticated(self) -> bool:
        """Check NILE authenticated."""
        if not self.cli_path:
            emit_external_auth_check_failed(
                self._bus,
                "amazon",
                "cli_not_found",
                "nile binary missing from search paths",
            )
            return False
        user_file = os.path.expanduser(
            get_cfg(
                self._config,
                "stores.amazon.user_file",
                "~/.config/nile/user.json",
            ),
        )
        if not os.path.isfile(user_file):
            return False
        try:
            with open(user_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(
                "[AmazonStore] user.json invalid: %s",
                e,
            )
            emit_external_auth_check_failed(
                self._bus,
                "amazon",
                "parse_error",
                f"{type(e).__name__}",
            )
            return False
        extensions = data.get("extensions", {})
        return "customer_info" in extensions

    async def start_auth(self, **kwargs) -> AuthResult:
        """Start auth."""
        if self._auth is None:
            return AuthResult(
                success=False,
                error="auth_not_configured",
                store="amazon",
            )
        await self._ensure_auth_shortcut()
        return cast("AuthResult", await self._auth.start_auth())

    async def complete_auth(self, code: str = "", **kwargs) -> AuthResult:
        """Complete auth."""
        if await self.is_available():
            return AuthResult(success=True, store="amazon")
        return AuthResult(
            success=False,
            error="not_authenticated",
            store="amazon",
        )

    async def logout(self) -> Result:
        """Logout."""
        if self._auth is None:
            await self._emit(
                Events.STORE_LOGOUT,
                store="amazon",
            )
            return Result(success=True)
        return await self._auth.logout()

    async def get_library(self) -> list[Game] | None:
        """Get library."""
        if not self.cli_path:
            return []
        try:
            owned = await self._library.read_owned_games()
            installed = await self._library.read_installed_ids()
            return merge_install_status(owned, installed)
        except Exception:
            logger.exception("[AmazonStore] get_library failed")
            return []

    async def install_game(
        self,
        game_id: str,
        base_path: str | None = None,
        progress_cb: ProgressCallback | None = None,
        **kwargs: Any,
    ) -> InstallResult:
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
        base_path = await self._updates.resolve_current_base_path(game_id)
        return await self._installer.install_game(
            game_id,
            base_path=base_path,
            progress_cb=progress_cb,
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
        """Ensure auth shortcut."""
        if self._shortcut_service is None:
            logger.debug(
                "[AmazonStore] no shortcut_service "
                "injected; skipping auth shortcut creation",
            )
            return
        launcher = os.path.join(
            self._plugin_dir or "",
            "py_modules",
            "unifideck",
            "launcher",
            "dispatcher.py",
        )
        if not await asyncio.to_thread(lambda: Path(launcher).is_file()):
            logger.warning(
                "[AmazonStore] launcher dispatcher not found at %s",
                launcher,
            )
            return
        result = await self._shortcut_service.add_auth_shortcut(
            store="amazon",
            launcher_path=launcher,
            title="Amazon Games Sign-In",
        )
        if not result.success:
            logger.warning(
                "[AmazonStore] add_auth_shortcut failed: %s",
                result.error,
            )
