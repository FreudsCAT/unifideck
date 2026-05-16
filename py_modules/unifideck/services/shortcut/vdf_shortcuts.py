"""services/shortcut/vdf_shortcuts.py — Escape-hatch read/write + auth delegator.

Provides direct access to the shortcuts list for the UI layer
and delegates auth shortcut creation to the shortcut.py helper.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from . import shortcut as _auth

if TYPE_CHECKING:
    from unifideck.core.types import Result
    # This is a mixin; `self` will be the ShortcutService facade at runtime.

logger = logging.getLogger(__name__)


class _VdfShortcutsMixin:
    """Escape-hatch read/write + auth shortcut delegator."""

    # These are provided by the ShortcutService facade at runtime
    _shortcuts: dict[str, Any]

    # Assume host provides these async load/save primitives
    # async def _load_shortcuts(self) -> None: ...
    # async def _save_all(self) -> None: ...

    async def read_shortcuts(self: Any) -> dict[str, Any]:
        """Return the raw shortcuts dictionary.

        Used by the UI layer to list/view all current shortcuts
        without making modifications.
        """
        await self._load_shortcuts()

        # We store internally as {"shortcuts": {"0": {}, "1": {}}}
        # Return a copy to avoid accidental external mutation
        if not isinstance(self._shortcuts, dict):
            return {"shortcuts": {}}

        return dict(self._shortcuts)

    async def write_shortcuts(self: Any, data: dict[str, Any]) -> None:
        """Overwrite the entire shortcuts dictionary and save.

        Used as an escape hatch for direct modifications.
        """
        self._shortcuts = dict(data)
        await self._save_all()

    async def add_auth_shortcut(
        self: Any,
        store: str,
        launcher_path: str,
        title: str,
    ) -> Result:
        """Create a hidden Steam shortcut for store OAuth login.

        Delegates to the ``build_auth_shortcut`` free function.
        """
        return await _auth.build_auth_shortcut(
            self,
            store,
            launcher_path,
            title,
        )
