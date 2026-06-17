"""services/shortcut/vdf_shortcuts.py — Escape-hatch read/write helpers.

Provides direct access to the shortcuts list for the UI layer.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _VdfShortcutsMixin:
    """Escape-hatch shortcut read/write."""

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
