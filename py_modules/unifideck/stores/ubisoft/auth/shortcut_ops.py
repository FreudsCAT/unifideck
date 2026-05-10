"""shortcut_ops.py — ShortcutService wrappers shared by the auth flow.

# OP-58f | py_modules/unifideck/stores/ubisoft/auth/shortcut_ops.py | Depends: (none)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....services.shortcut import ShortcutService
    from ..config import UbisoftConfig

_LEGACY_AUTH_SHORTCUT_STORE_ID = 'ubisoft:.template'
logger = logging.getLogger(__name__)


class _ShortcutRegistryOps:
    """Shortcut registry ops."""

    def __init__(self, *, config: UbisoftConfig) -> None:
        """Initialize the instance."""
        self._config = config

    async def load(self, sm: ShortcutService) -> dict[str, Any]:
        """Load."""
        try:
            return await sm.get_registry()
        except Exception as e:
            logger.warning('[Ubisoft.auth] registry load failed: %s', e)
            return {}

    async def register(
        self, sm: ShortcutService, appid: int, name: str,
    ) -> None:
        """Register."""
        try:
            await sm.register_appid(
                store_id=self._config.auth_shortcut_store_id,
                appid=appid, name=name,
            )
        except Exception as e:
            logger.warning('[Ubisoft.auth] registry register failed: %s', e)

    async def clear_compat(
        self, sm: ShortcutService, appid: int,
    ) -> None:
        """Clear compat."""
        try:
            await sm.clear_compat_tool(appid)
        except Exception as e:
            logger.debug('[Ubisoft.auth] clear_compat failed: %s', e)

    async def cleanup_legacy(self, sm: ShortcutService) -> None:
        """Cleanup legacy."""
        try:
            await sm.unregister_store_id(_LEGACY_AUTH_SHORTCUT_STORE_ID)
        except Exception as e:
            logger.debug('[Ubisoft.auth] legacy cleanup failed: %s', e)
