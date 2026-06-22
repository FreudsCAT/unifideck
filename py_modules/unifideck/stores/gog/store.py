"""GOG store — Layer-4 implementation of the unified store interface.

OP-50a | py_modules/unifideck/stores/gog/store.py

``GOGStore`` is the orchestration class that wires every sub-component
of the GOG sub-package together and exposes them through the
``StoreBase`` contract used by the rest of the plugin (RPC mixins,
service layer, registry). It owns one instance each of:

* ``GOGConfig`` (OP-50b)         — frozen configuration snapshot.
* ``GOGTokenManager`` (OP-52a)   — OAuth tokens + persistence.
* ``GOGLibrary`` (OP-50c)        — owned-games library facade.
* ``GOGInstaller`` (OP-51a)      — install/uninstall pipeline.
* ``GOGUpdatesChecker`` (OP-50g) — update polling.
* ``GOGDlcManager`` (OP-50f)     — DLC enumeration + install.
* ``GOGBrowserAuth`` (OP-50h)    — embedded-browser OAuth flow.
* ``GOGExeResolver`` (OP-50e)    — locate the launchable .exe.

Implements the standard ``StoreBase`` API: ``store_info``, ``is_authed``,
``auth``, ``logout``, ``library``, ``install``, ``uninstall``, ``launch``,
etc. — every method is delegated to the appropriate sub-component.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from unifideck.auth.browser import OAuthBrowserMonitor
from unifideck.auth.edge_browser import EdgeBrowser
from unifideck.auth.orchestrator import AuthOrchestrator
from unifideck.core.safe_delete import canonical_prefix, safe_rmtree
from unifideck.core.types import (
    AuthResult,
    Events,
    Game,
    InstallResult,
    Result,
    StoreInfo,
)
from unifideck.services.shortcut import ShortcutService
from unifideck.stores.shared.store_base import StoreBase
from unifideck.utils.locale import get_unifideck_locale

from .auth import GOGBrowserAuth
from .config import GOG_AUTH_URL_FILE, GOGConfig
from .dlc import GOGDlcManager
from .exe_resolver import GOGExeResolver
from .install import GOGInstaller
from .library import GOGLibrary
from .tokens import GOGTokenManager
from .updates import GOGUpdatesChecker

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus.event_bus import EventBus
logger = logging.getLogger(__name__)


class GOGStore(StoreBase):
    """Gogstore."""

    store_info = StoreInfo(
        name="gog",
        display_name="GOG",
        auth_method="oauth",
        icon_asset="gog.png",
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
        self._gog_config: GOGConfig = GOGConfig.from_config_manager(config)
        logger.info(
            "[GOGStore] %s",
            self._gog_config.describe(),
        )
        self._config_manager = config
        self._shortcut_service = shortcut_service
        self._edge = edge_browser
        self._tokens = GOGTokenManager(self._gog_config, bus=bus)
        self._exe = GOGExeResolver()

        self._library = GOGLibrary(
            config=self._gog_config,
            tokens=self._tokens,
            exe_finder=self._exe.find,
        )
        # Auth is late-bound : at boot ``browser_monitor`` is
        # ``None`` (auto-discovery doesn't see the service
        # container yet). The injector sets ``_browser_monitor``
        # post-discovery and calls
        # ``_rebuild_auth_after_injection`` so the flow gets
        # wired against the just-injected monitor.
        self._browser_monitor = browser_monitor
        self._auth: GOGBrowserAuth | None = None
        self._rebuild_auth_after_injection()
        if self._auth is None:
            gogdl_bin = self._resolve_gogdl_bin()
            self._installer = GOGInstaller(
                config=self._gog_config,
                tokens=self._tokens,
                gogdl_bin=gogdl_bin,
                exe_finder=self._exe.find,
                locale_fn=lambda: get_unifideck_locale(
                    self._config_manager,
                ),
            )
            self._dlc = GOGDlcManager(
                config=self._gog_config,
                tokens=self._tokens,
                gogdl_bin=gogdl_bin,
                locale_fn=lambda: get_unifideck_locale(
                    self._config_manager,
                ),
                resolve_install_path=self._library.get_installed_game_info,
            )
            self._updates = GOGUpdatesChecker(
                config=self._gog_config,
                tokens=self._tokens,
                gogdl_bin=gogdl_bin,
                get_installed_ids=self._library.get_installed,
                resolve_install_info=self._library.get_installed_game_info,
            )

    def _rebuild_auth_after_injection(self) -> None:
        """(Re-)build the GOG browser-auth flow once a monitor is set.

        Called by `store_injector` after the OAuth browser
        monitor has been wired into the container. Idempotent —
        early-returns if `_auth` is already built.
        """
        if self._auth is not None:
            return
        monitor = getattr(self, "_browser_monitor", None)
        if monitor is None:
            logger.debug(
                "[GOGStore] no browser_monitor; auth disabled",
            )
            return
        orchestrator = AuthOrchestrator(
            bus=self._bus,
            browser_monitor=monitor,
            store_name="gog",
        )
        self._auth = GOGBrowserAuth(
            bus=self._bus,
            orchestrator=orchestrator,
            tokens=self._tokens,
            config=self._gog_config,
        )
        # Rebuild the gogdl-driven submodules so they reference
        # the live token manager — `_auth` may have refreshed
        # tokens in the meantime.
        gogdl_bin = self._resolve_gogdl_bin()
        self._installer = GOGInstaller(
            config=self._gog_config,
            tokens=self._tokens,
            gogdl_bin=gogdl_bin,
            exe_finder=self._exe.find,
            locale_fn=lambda: get_unifideck_locale(
                self._config_manager,
            ),
        )
        self._dlc = GOGDlcManager(
            config=self._gog_config,
            tokens=self._tokens,
            gogdl_bin=gogdl_bin,
            locale_fn=lambda: get_unifideck_locale(
                self._config_manager,
            ),
            resolve_install_path=self._library.get_installed_game_info,
        )
        self._updates = GOGUpdatesChecker(
            config=self._gog_config,
            tokens=self._tokens,
            gogdl_bin=gogdl_bin,
            get_installed_ids=self._library.get_installed,
            resolve_install_info=self._library.get_installed_game_info,
        )
        logger.info("[GOGStore] auth flow wired")

    async def is_available(self) -> bool:
        """Check whether available."""
        if not self._gog_config.is_valid():
            self._cached_available = False
            return False
        available = await self._library.is_available()
        self._cached_available = available
        return available

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        if self._auth is None:
            return AuthResult(
                success=False,
                error="auth_not_configured",
                store="gog",
            )
        if self._edge is None or not self._edge.is_installed:
            logger.info(
                "[GOGStore] Edge not installed — prompting user",
            )
            return AuthResult(
                success=False,
                error="edge_not_installed",
                store="gog",
            )
        self._edge.clear_store_cookies("gog.com")
        return cast("AuthResult", await self._auth.start_auth())

    async def complete_auth(self, code: str = "", **kwargs: Any) -> AuthResult:
        """Complete auth."""
        if await self.is_available():
            return AuthResult(success=True, store="gog")
        return AuthResult(
            success=False,
            error="not_authenticated",
            store="gog",
        )

    async def logout(self) -> Result:
        """Logout."""
        if self._auth is not None:
            result = await self._auth.logout(
                browser_monitor=self._browser_monitor_from_auth(),
            )
        else:
            await self._tokens.clear()
            await self._bus.emit(
                Events.STORE_LOGOUT,
                store="gog",
            )
            result = Result(success=True)
        auth_url_file = await asyncio.to_thread(lambda: str(Path(GOG_AUTH_URL_FILE).expanduser()))
        if await asyncio.to_thread(lambda: Path(auth_url_file).is_file()):
            try:
                # ``Path.unlink`` is blocking I/O — wrap in to_thread.
                await asyncio.to_thread(lambda: Path(auth_url_file).unlink())
            except OSError as e:
                logger.warning(
                    "[GOGStore] could not remove %s: %s",
                    auth_url_file,
                    e,
                )
        return result

    async def get_library(self, *, force: bool = False) -> list[Game] | None:
        """Get library."""
        return await self._library.fetch_library()

    async def install_game(
        self,
        game_id: str,
        base_path: str | None = None,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        language: str | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Install game."""
        return await self._installer.install_game(
            game_id=game_id,
            base_path=base_path,
            progress_cb=progress_cb,
            language=language,
        )

    async def uninstall_game(
        self, game_id: str, delete_prefix: bool = False, **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        info = self._library.get_installed_game_info(game_id)
        install_path = info.get("install_path") if info else None
        result = await self._installer.uninstall_game(
            game_id=game_id,
            install_path=install_path,
        )
        # Emit so the shortcut service flips this game's Steam
        # shortcut to "Not Installed" and prunes games.map — Epic
        # and Amazon already do this; GOG previously did not, so
        # the shortcut stayed marked installed after a successful
        # uninstall.
        if result.success:
            # GOG games run under Proton (umu) with a per-game prefix at the
            # canonical flat location. The old signature swallowed
            # ``delete_prefix`` via ``**kwargs``, so the prefix (~1.6 GB)
            # leaked even when the user ticked "also delete Proton prefix".
            if delete_prefix:
                await asyncio.to_thread(
                    safe_rmtree, canonical_prefix(game_id),
                )
            await self._emit(
                Events.GAME_UNINSTALLED,
                store="gog",
                game_id=game_id,
            )
        return result

    async def update_game(
        self,
        game_id: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Update game."""
        result = await self._updates.update_game(game_id)
        return InstallResult(
            success=result.success,
            error=result.error,
            store="gog",
            game_id=game_id,
        )

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        return await self._updates.check_for_updates()

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        size = await self._installer._planner.get_expected_disk_size(
            game_id,
            "windows",
        )
        return size if size > 0 else None

    async def get_installed_path(self, game_id: str) -> str | None:
        """On-disk install dir for an installed GOG game.

        Lets the App-Details "Installed size" find the real directory
        when the sync cache's ``install_path`` is missing/stale. The
        library scan is filesystem I/O, so run it off the event loop.
        """
        info = await asyncio.to_thread(
            self._library.get_installed_game_info, game_id,
        )
        path = info.get("install_path") if isinstance(info, dict) else None
        return path if isinstance(path, str) and path else None

    async def get_game_dlcs(self, game_id: str) -> list[dict[str, Any]]:
        """Get game dlcs."""
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
        """Install dlc."""
        return await self._dlc.install_dlc(
            game_id=game_id,
            dlc_id=dlc_id,
            base_path=base_path,
            progress_cb=progress_cb,
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

    def get_installed_game_info(self, game_id: str) -> dict[str, str | None] | None:
        """Get installed game info."""
        return self._library.get_installed_game_info(game_id)

    def find_installed_exe(
        self, install_path: str, game_id: str | None = None,
    ) -> str | None:
        """Resolve the launchable target for an installed GOG game.

        ``game_id`` is accepted for a uniform store interface (the
        download worker passes it) but unused — GOGExeResolver works
        from the install dir alone.

        Used by ``DownloadWorker._build_installed_game`` to populate
        ``Game.exe_path`` (and thus the ``games.map`` entry the
        launcher reads). GOG Linux-native games launch via a
        ``start.sh`` wrapper, which the generic ``StoreBase._find_exe``
        heuristic (``.exe``-only) misses — delegating to
        ``GOGExeResolver`` handles start.sh, goggame play tasks, and
        Windows ``.exe`` targets alike. Without this, GOG games landed
        in ``games.map`` with an empty exe and silently failed to launch.
        """
        return self._exe.find(install_path)

    def migrate_old_markers(self) -> dict[str, int]:
        """Migrate old markers."""
        return self._library.migrate_old_markers()

    def _resolve_gogdl_bin(self) -> str:
        """Resolve GOGDL bin."""
        if not self._plugin_dir:
            logger.warning(
                "[GOGStore] no plugin_dir; gogdl path unresolvable",
            )
            return ""
        path = str(Path(self._plugin_dir) / "bin" / "gogdl")
        if not Path(path).is_file():
            logger.warning(
                "[GOGStore] gogdl binary not found at %s",
                path,
            )
        else:
            logger.info(
                "[GOGStore] using gogdl at %s",
                path,
            )
        return path

    def _browser_monitor_from_auth(self) -> OAuthBrowserMonitor | None:
        """Browser monitor from auth."""
        if self._auth is None:
            return None
        try:
            return self._auth._orch._monitor
        except AttributeError:
            return None
