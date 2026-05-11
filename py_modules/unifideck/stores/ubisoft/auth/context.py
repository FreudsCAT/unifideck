"""context.py — Build the auth-context dict the frontend renders.

# OP-58b | py_modules/unifideck/stores/ubisoft/auth/context.py | Depends: (none)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .facade import UbisoftAuth

logger = logging.getLogger(__name__)
_SGDB_UBISOFT_CONNECT_ID = 5270094
_AUTH_SHORTCUT_NAME = 'Ubisoft Connect'


class _AuthContext:
    """Auth context."""

    def __init__(self, parent: UbisoftAuth) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def fetch_auth_shortcut_artwork(
        self, unsigned_id: int, force: bool = False,
    ) -> None:
        """Fetch auth shortcut artwork."""
        services = self._parent._services
        sgdb = services.steamgriddb
        if sgdb is None:
            return
        try:
            await sgdb.fetch_artwork_for_appid(
                appid=unsigned_id,
                game_id=_SGDB_UBISOFT_CONNECT_ID,
                force=force,
            )
        except Exception as e:
            logger.debug('[Ubisoft.auth] artwork fetch failed: %s', e)

    def build_auth_context_success(
        self, unsigned_appid: int, *, with_launch_wait: bool = True,
    ) -> dict[str, Any]:
        """Build auth context success."""
        config = self._parent._state.config
        ctx: dict[str, Any] = {
            'success': True,
            'shortcut_appid': unsigned_appid,
            'store_id': config.auth_shortcut_store_id,
            'name': _AUTH_SHORTCUT_NAME,
        }
        if with_launch_wait:
            ctx['launch_wait_ms'] = config.auth_shortcut_launch_wait_ms
        return ctx

    async def get_auth_shortcut_context(self) -> dict[str, Any]:
        """Get auth shortcut context."""
        services = self._parent._services
        sm = services.shortcut_service
        if sm is None:
            return {'success': False, 'error': 'shortcut_service_unavailable'}
        existing = await self._try_existing_registry(sm)
        if existing is not None:
            return self.build_auth_context_success(existing)
        unsigned = await self._parent.ensure_auth_shortcut()
        if unsigned is None:
            return {'success': False, 'error': 'auth_shortcut_unavailable'}
        return self.build_auth_context_success(unsigned)

    async def _try_existing_registry(self, sm: Any) -> dict[str, Any] | None:
        """Try existing registry."""
        try:
            registry = await sm.get_registry()
        except Exception:
            return None
        config = self._parent._state.config
        entry = registry.get(config.auth_shortcut_store_id)
        if not isinstance(entry, dict):
            return None
        appid = entry.get('appid_unsigned') or entry.get('appid')
        try:
            return int(appid) if appid is not None else None
        except (TypeError, ValueError):
            return None
