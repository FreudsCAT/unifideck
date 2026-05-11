"""
Ubisoft authentication facade — orchestrates the Steam-shortcut auth flow.

OP-58a | py_modules/unifideck/stores/ubisoft/auth/facade.py

Ubisoft Connect has no headless auth flow: the user must sign in
through the UPC GUI. The trick we use is to create a Steam shortcut
that launches UPC inside a dedicated auth prefix; once the user signs
in, UPC writes credentials to the prefix and we propagate them to
every game prefix afterwards (via ``UbisoftSession``, OP-60a).

``UbisoftAuth`` is the orchestration class that wires together the four
sub-modules: ``context`` (UI payload), ``shortcut`` (Steam shortcut
creation), ``session_monitor`` (signal on credential file appearance),
``direct_signin`` (fallback for already-signed-in installs).

``UbisoftAuthState`` and ``UbisoftAuthServices`` are frozen dataclasses
holding the dependencies. State is "owned data" (config, paths,
binaries, callbacks); Services are "external system handles"
(shortcut_service, steamgriddb).
"""

from __future__ import annotations
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from ....core.types import AuthResult, Events, Result
from ....security import (
    audit_auth_flow,
)
from ..binaries import UbisoftBinaryResolver
from ..config import UbisoftConfig
from ..paths import UbisoftPrefixPaths
from ..session import UbisoftSession
from .context import _AuthContext
from .direct_signin import _DirectSignIn
from .session_monitor import _AuthSessionMonitor
from .shortcut import _AuthShortcut
from .shortcut_ops import _ShortcutRegistryOps

if TYPE_CHECKING:
    from ....event_bus.event_bus import EventBus
    from ....services.shortcut import ShortcutService
    from ....steam.steamgriddb import SteamGridDBClient
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UbisoftAuthState:
    """Ubisoft auth state."""

    config: UbisoftConfig
    paths: UbisoftPrefixPaths
    binaries: UbisoftBinaryResolver
    session: UbisoftSession
    ensure_auth_prefix: Callable[[], Any]
    queue_auth_assets_ensure: Callable[[str], None]


@dataclass(frozen=True)
class UbisoftAuthServices:
    """Ubisoft auth services."""

    plugin_dir: str | None
    shortcut_service: ShortcutService | None
    steamgriddb: SteamGridDBClient | None


class UbisoftAuth:
    """Ubisoft auth."""

    def __init__(
        self,
        bus: EventBus,
        state: UbisoftAuthState,
        services: UbisoftAuthServices,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._config = state.config
        self._paths = state.paths
        self._binaries = state.binaries
        self._session = state.session
        self._ensure_auth_prefix = state.ensure_auth_prefix
        self._queue_auth_assets_ensure = state.queue_auth_assets_ensure
        self._plugin_dir = services.plugin_dir
        self._shortcut_service = services.shortcut_service
        self._steamgriddb = services.steamgriddb
        self._registry_ops = _ShortcutRegistryOps(config=self._config)
        self._monitor = _AuthSessionMonitor(
            config=self._config,
            session=self._session,
            queue_auth_assets_ensure=self._queue_auth_assets_ensure,
        )
        self._direct_signin = _DirectSignIn(
            binaries=self._binaries,
            bus=self._bus,
            config=self._config,
            paths=self._paths,
            session=self._session,
            ensure_auth_prefix=self._ensure_auth_prefix,
            queue_auth_assets_ensure=self._queue_auth_assets_ensure,
        )
        self._shortcut = _AuthShortcut(self)
        self._context = _AuthContext(self)

    async def ensure_auth_shortcut(self) -> int | None:
        """Ensure auth shortcut."""
        return await self._shortcut.ensure_auth_shortcut()

    async def auth_shortcut_exists_in_vdf(self) -> bool:
        """Auth shortcut exists in VDF."""
        return await self._shortcut.auth_shortcut_exists_in_vdf()

    async def fetch_auth_shortcut_artwork(
        self,
        unsigned_id: int,
        force: bool = False,
    ) -> None:
        """Fetch auth shortcut artwork."""
        await self._context.fetch_auth_shortcut_artwork(
            unsigned_id,
            force=force,
        )

    async def get_auth_shortcut_context(self) -> dict[str, Any]:
        """Get auth shortcut context."""
        return await self._context.get_auth_shortcut_context()

    async def is_available(self) -> bool:
        """Check whether available."""
        auth_dir = self._config.auth_prefix_dir_expanded
        return self._session.has_valid_credentials(auth_dir)

    @audit_auth_flow(store="ubisoft", method="wine_installer")
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        return AuthResult(
            success=True,
            store="ubisoft",
            metadata={
                "auth_type": "upc_launch",
                "message": "Sign in through Ubisoft Connect",
            },
        )

    async def complete_auth(
        self,
        code: str = "",
        **kwargs: Any,
    ) -> AuthResult:
        """Complete auth."""
        if await self.is_available():
            return AuthResult(
                success=True,
                store="ubisoft",
            )
        return AuthResult(
            success=False,
            store="ubisoft",
            error="not_authenticated",
        )

    async def logout(self) -> Result:
        """Logout."""
        self._session.clear_session_file()
        auth_dir = self._config.auth_prefix_dir_expanded
        if os.path.isdir(auth_dir):
            try:
                shutil.rmtree(auth_dir)
                logger.info(
                    "[UbisoftAuth] removed auth prefix directory",
                )
            except OSError as e:
                logger.error(
                    "[UbisoftAuth] could not remove auth prefix: %s",
                    e,
                )
        await self._bus.emit(
            Events.STORE_LOGOUT,
            store="ubisoft",
        )
        return Result(success=True)

    async def start_auth_session_monitor(self) -> Result:
        """Start auth session monitor."""
        return await self._monitor.start()

    def check_auth_session_status(self) -> dict[str, Any]:
        """Check auth session status."""
        return self._monitor.status()

    async def connect_ubisoft_account(self) -> dict[str, Any]:
        """Connect UBISOFT account."""
        return await self._direct_signin.connect()

    async def _load_registry(self, sm: ShortcutService) -> dict[str, Any]:
        """Load registry."""
        return await self._registry_ops.load(sm)

    async def _register_shortcut(
        self,
        sm: ShortcutService,
        appid: int,
        name: str,
    ) -> None:
        """Register shortcut."""
        await self._registry_ops.register(sm, appid, name)

    async def _clear_compat(
        self,
        sm: ShortcutService,
        appid: int,
    ) -> None:
        """Clear compat."""
        await self._registry_ops.clear_compat(sm, appid)

    async def _cleanup_legacy_registry(self, sm: ShortcutService) -> None:
        """Cleanup legacy registry."""
        await self._registry_ops.cleanup_legacy(sm)
