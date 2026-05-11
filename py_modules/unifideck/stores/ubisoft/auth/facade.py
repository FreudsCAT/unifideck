"""facade.py — Public Ubisoft auth surface.

# OP-58a | py_modules/unifideck/stores/ubisoft/auth/facade.py | Depends: OP-55a

Glues the five internal auth helpers (context, shortcut, session-monitor,
direct-signin, shortcut-ops) behind a single ``UbisoftAuth`` class. The
state and services frozen-dataclasses keep the constructor wide-but-flat
so the store layer can wire dependencies without star-args.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....core.types import AuthResult, Events, Result
from ....security import audit_auth_flow
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
        self._state = state
        self._services = services
        self._context = _AuthContext(self)
        self._shortcut = _AuthShortcut(self)
        self._monitor = _AuthSessionMonitor(
            config=state.config,
            session=state.session,
            queue_auth_assets_ensure=state.queue_auth_assets_ensure,
        )
        self._direct = _DirectSignIn(
            binaries=state.binaries,
            bus=bus,
            config=state.config,
            paths=state.paths,
            session=state.session,
            ensure_auth_prefix=state.ensure_auth_prefix,
            queue_auth_assets_ensure=state.queue_auth_assets_ensure,
        )
        self._registry_ops = _ShortcutRegistryOps(config=state.config)

    async def ensure_auth_shortcut(self) -> int | None:
        """Ensure auth shortcut."""
        return await self._shortcut.ensure_auth_shortcut()

    async def auth_shortcut_exists_in_vdf(self) -> bool:
        """Auth shortcut exists in VDF."""
        return await self._shortcut.auth_shortcut_exists_in_vdf()

    async def fetch_auth_shortcut_artwork(
        self, unsigned_id: int, force: bool = False,
    ) -> None:
        """Fetch auth shortcut artwork."""
        await self._context.fetch_auth_shortcut_artwork(unsigned_id, force)

    async def get_auth_shortcut_context(self) -> dict[str, Any]:
        """Get auth shortcut context."""
        return await self._context.get_auth_shortcut_context()

    async def is_available(self) -> bool:
        """Check whether available."""
        try:
            return any(
                self._state.session.has_valid_credentials(p)
                for p in self._state.config.iter_game_prefix_paths()
            ) or self._state.session.has_valid_credentials(
                self._state.config.auth_prefix_dir_expanded,
            )
        except Exception as e:
            logger.debug('[Ubisoft.auth] is_available failed: %s', e)
            return False

    @audit_auth_flow(store='ubisoft', method='wine_installer')
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        ctx = await self.get_auth_shortcut_context()
        if not ctx.get('success'):
            return AuthResult(
                success=False,
                error=str(ctx.get('error', 'auth_unavailable')),
                store='ubisoft',
            )
        return AuthResult(
            success=True,
            store='ubisoft',
            redirect_url=None,
            extra={'shortcut_context': ctx},
        )

    async def complete_auth(self, code: str = '', **kwargs: Any) -> AuthResult:
        """Complete auth."""
        if await self.is_available():
            await self._bus.emit(Events.STORE_AUTH_COMPLETE, store='ubisoft')
            return AuthResult(success=True, store='ubisoft')
        return AuthResult(
            success=False, store='ubisoft', error='credentials_not_captured',
        )

    async def logout(self) -> Result:
        """Logout."""
        try:
            self._state.session.clear_session_file()
        except Exception as e:
            logger.warning('[Ubisoft.auth] logout failed: %s', e)
            return Result(success=False, error=str(e))
        await self._bus.emit(Events.STORE_LOGOUT, store='ubisoft')
        return Result(success=True)

    async def start_auth_session_monitor(self) -> Result:
        """Start auth session monitor."""
        return await self._monitor.start()

    def check_auth_session_status(self) -> dict[str, Any]:
        """Check auth session status."""
        return self._monitor.status()

    async def connect_ubisoft_account(self) -> dict[str, Any]:
        """Connect UBISOFT account."""
        return await self._direct.connect()

    async def _load_registry(self, sm: ShortcutService) -> dict[str, Any]:
        """Load registry."""
        return await self._registry_ops.load(sm)

    async def _register_shortcut(
        self, sm: ShortcutService, appid: int, name: str,
    ) -> None:
        """Register shortcut."""
        await self._registry_ops.register(sm, appid, name)

    async def _clear_compat(self, sm: ShortcutService, appid: int) -> None:
        """Clear compat."""
        await self._registry_ops.clear_compat(sm, appid)

    async def _cleanup_legacy_registry(self, sm: ShortcutService) -> None:
        """Cleanup legacy registry."""
        await self._registry_ops.cleanup_legacy(sm)

    async def _fetch_auth_shortcut_artwork(
        self, unsigned_id: int, force: bool = False,
    ) -> None:
        """Internal alias used by _AuthShortcut."""
        await self._context.fetch_auth_shortcut_artwork(unsigned_id, force)
