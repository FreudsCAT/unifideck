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

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from unifideck.core.types import AuthResult, Events, Game, InstallResult, Result, StoreInfo
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe
from unifideck.stores.shared.store_base import StoreBase

from .specialists import build_ubisoft_specialists

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.services.shortcut import ShortcutService
    from unifideck.steam.steamgriddb import SteamGridDBClient

    from .auth import UbisoftAuth
    from .config import UbisoftConfig
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
        # Drift fix (lot 11g): ``self._config`` is set by
        # ``super().__init__`` to ``ConfigManager | None``; we
        # then shadow it with the specialist ``UbisoftConfig``.
        # Annotate the new shape explicitly so mypy doesn't
        # report ``Incompatible types in assignment``.
        self._config: UbisoftConfig = specialists.config  # type: ignore[assignment]
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
        # Subscribe to bus events (currently GAME_STOPPED, to capture the token
        # UPC rotated during a play session back to the auth prefix).
        auto_wire(self, bus)

    @subscribe(Events.GAME_STOPPED)
    async def _capture_upc_session_on_stop(self, **kwargs: Any) -> None:
        """Capture the token UPC rotated during play back to the auth prefix.

        The Play path runs in the launcher subprocess, which can't reach the
        backend session facade — so a token UPC rotates while the game runs is
        written only into the game prefix and never makes it back to
        ``.upc-auth``. Left uncaptured, the auth prefix ends up on a
        server-stale token and the next FRESH install (or a game installed to a
        new prefix) opens signed-out. Capturing on game-stop keeps auth current
        after every play. ``capture()`` is guarded (auth-only, skips a
        logged-out / smaller source), so a normal exit that didn't rotate — or
        an explicit logout — is a safe no-op.
        """
        if kwargs.get("store") != "ubisoft":
            return
        game_id = kwargs.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            return
        try:
            prefix_path = self._paths.get_prefix_path(game_id)
            captured = await asyncio.to_thread(
                self._session.capture, prefix_path,
            )
            if captured:
                await asyncio.to_thread(self._session.propagate_all_to_all)
                logger.info(
                    "[UbisoftStore] captured rotated UPC token after play "
                    "for %s → auth refreshed",
                    game_id,
                )
            else:
                await self._warn_if_stored_credential_is_dead(
                    game_id, prefix_path,
                )
        except Exception as e:
            logger.warning(
                "[UbisoftStore] post-play session capture failed: %s", e,
            )

    async def _warn_if_stored_credential_is_dead(
        self, game_id: str, prefix_path: str,
    ) -> None:
        """Tell the user when re-signing-in is the only way out.

        ``capture`` returning nothing is normally a healthy no-op. But when the
        prefix we injected a signed-in credential into comes back signed OUT,
        the stored token is dead server-side and every future install will
        inject it again — a silent loop of sign-in prompts with no UI affordance
        to break it. Say so, and name the one action that fixes it.

        Report-only: the credential is never purged here. Sign-out is
        destructive and stays the user's decision.
        """
        try:
            dead = await asyncio.to_thread(
                self._session.stored_credential_was_rejected, prefix_path,
            )
        except Exception:
            logger.exception("[UbisoftStore] credential health check failed")
            return
        if not dead:
            return
        logger.warning(
            "[UbisoftStore] UPC signed out of %s despite the injected "
            "credential — the stored Ubisoft token is no longer accepted. "
            "Sign out and back in (QAM → Ubisoft) to replace it; until then "
            "every install will keep asking for a sign-in.",
            game_id,
        )
        with contextlib.suppress(Exception):
            await self._bus.emit(
                Events.STORE_AUTH_FAILED,
                store="ubisoft",
                error=(
                    "Ubisoft sign-in expired — sign out and back in to "
                    "reconnect your account"
                ),
            )

    def _rebuild_auth_after_injection(self) -> None:
        """Wire the post-injection shortcut service into the auth facade.

        Auto-discovery builds the store — and its auth facade — before
        the service container exists, so the facade captured
        ``shortcut_service=None``. ``store_injector`` sets
        ``self._shortcut_service`` afterward and invokes this hook;
        without it the facade keeps the ``None`` and
        ``get_auth_shortcut_context`` returns
        ``shortcut_service_unavailable`` — surfaced in the QAM as
        "Auth shortcut not available", which blocks sign-in entirely.
        The facade's sub-objects (``_context``/``_shortcut``/
        ``_registry_ops``) all read ``self._parent._shortcut_service``
        dynamically, so re-pointing the single facade attribute wires
        the whole auth flow.
        """
        shortcut_service = getattr(self, "_shortcut_service", None)
        if shortcut_service is None:
            return
        self._auth._shortcut_service = shortcut_service
        logger.info(
            "[UbisoftStore] shortcut service wired into auth post-injection",
        )

    async def is_available(self) -> bool:
        """Check whether available."""
        available = await self._auth.is_available()
        self._cached_available = available
        return available

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Start auth."""
        await self._auth.ensure_auth_shortcut()
        # The auth prefix (.upc-auth) must exist before UPC
        # can launch. First-time setup downloads the UPC
        # installer and creates the Wine prefix — may take
        # several minutes. Subsequent calls are a no-op.
        await self._prefix_mgr.ensure_auth_prefix()
        await self._auth.start_auth_session_monitor()
        return cast("AuthResult", await self._auth.start_auth())

    async def complete_auth(self, **kwargs: Any) -> AuthResult:
        """Complete auth — succeeds once UPC has captured credentials.

        Ubisoft has no code/2FA step: sign-in happens entirely inside
        the UPC GUI in the auth prefix. This just confirms credentials
        landed on disk.
        """
        return await self._auth.complete_auth(**kwargs)

    async def logout(self) -> Result:
        """Logout."""
        return await self._auth.logout()

    async def get_library(self, *, force: bool = False) -> list[Game] | None:
        """Get library.

        ``force`` (force-sync) re-pulls the unifiDB lookup tables.

        Gated on authentication (mirrors ``MicrosoftStore.get_library``).
        Without a signed-in UPC session the library facade falls back to
        the local UPC binaries — which list *every* configured Ubisoft
        title, not the ones the user owns — and the bootstrap-marker
        install scan flags them ``installed`` even though they can't
        launch. Returning early keeps those phantom entries out of the
        library; the install scan re-surfaces real games the moment the
        user signs in.
        """
        if not await self.is_available():
            logger.info(
                "[UbisoftStore] not authenticated — returning empty library",
            )
            return []
        return await self._library.get_library(force=force)

    async def install_game(
        self,
        game_id: str,
        *,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        install_path: str | None = None,
        on_ready: Callable[[], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Install game.

        ``on_ready`` (used by the download worker) fires once the
        per-game prefix is bootstrapped and UPC is ready to open — the
        worker emits the frontend RunGame request from it. See
        ``UbisoftInstaller.install_game``.
        """
        return await self._installer.install_game(
            game_id,
            progress_cb=progress_cb,
            install_path=install_path,
            on_ready=on_ready,
        )

    async def uninstall_game(
        self,
        game_id: str,
        *,
        delete_prefix: bool = False,
        **kwargs: Any,
    ) -> Result:
        """Uninstall game."""
        result = await self._installer.uninstall_game(
            game_id,
            delete_prefix=delete_prefix,
        )
        # Emit so the shortcut service flips this game's Steam
        # shortcut to "Not Installed" and prunes games.map — Epic
        # and Amazon already do this; Ubisoft previously did not, so
        # the shortcut stayed marked installed after a successful
        # uninstall.
        if result.success:
            await self._emit(
                Events.GAME_UNINSTALLED,
                store="ubisoft",
                game_id=game_id,
            )
        return result

    async def update_game(
        self,
        game_id: str,
        **kwargs: Any,
    ) -> InstallResult:
        """Update game.

        Deliberately ignores the ``on_ready`` the wrapper dispatch now offers:
        this path still spawns UPC from the backend (``update_op``) instead of
        asking the frontend to ``RunGame`` it. That is a pre-existing gap — a
        backend-spawned window does not render in Gaming Mode — and declining
        the hook is a store's choice, not a branch in shared code. Converting
        it to the watcher is a separate change; ``install_game`` already works
        that way.
        """
        del kwargs
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

    async def get_installed_path(self, game_id: str) -> str | None:
        """On-disk install dir for an installed Ubisoft game.

        Lets the App-Details "Installed size" find the real directory
        when the sync cache's ``install_path`` is missing/stale. The
        prefix/library scan is filesystem I/O, so run it off the loop.
        """
        info = await asyncio.to_thread(
            self._library.get_installed_game_info, game_id,
        )
        path = info.get("install_path") if isinstance(info, dict) else None
        return path if isinstance(path, str) and path else None

    def get_prefix_path(self, game_id: str) -> str | None:
        """The game's Wine prefix — for this store, the whole install footprint.

        UPC installs into ``<prefix>/drive_c/Program Files (x86)/Ubisoft/…``
        and uninstalling removes the prefix, so the prefix is both what the
        game costs on disk and what the user gets back.

        This resolves through ``paths``, which prefers the recorded location
        and falls back to the internal default for games installed before that
        was recorded. The "recorded, never reconstructed" rule that governs the
        prefix elsewhere guards a *destructive* site — a rebuilt path once
        stamped a marker into a directory no launch had opened and produced a
        permanent reset loop. Here the path is only walked for a byte count,
        and the caller drops it unless the directory exists.
        """
        prefix = self._paths.get_prefix_path(game_id)
        return prefix if isinstance(prefix, str) and prefix else None

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
