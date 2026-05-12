"""Steam shortcuts.vdf read/write — binary VDF format.

OP-14f | py_modules/unifideck/services/shortcut/vdf_shortcuts.py

``_VdfShortcutsMixin`` is the I/O layer for ``shortcuts.vdf``. Steam
uses a custom binary format (BVDF) we serialise with the bundled
``vdf`` library. The mixin handles :

* lockfile-protected reads/writes (Steam locks the file while editing);
* atomic writes (temp + rename) to avoid corruption if the plugin
  crashes mid-write;
* schema migrations (Steam occasionally adds new fields).
"""

from __future__ import annotations
import logging
from typing import Any
from . import auth_shortcut as _auth

logger = logging.getLogger(__name__)


class _VdfShortcutsMixin:
    """Vdf shortcuts mixin."""

    _shortcuts: list[dict[str, Any]]

    async def read_shortcuts(self) -> dict[str, Any]:
        """Read shortcuts."""
        await self._load_shortcuts()
        return {
            "shortcuts": {str(i): entry for i, entry in enumerate(self._shortcuts)},
        }

    async def write_shortcuts(self, data: dict[str, Any]) -> None:
        """Write shortcuts."""
        shortcuts_map = data.get("shortcuts", {})
        self._shortcuts = list(shortcuts_map.values())
        await self._save_all()

    async def add_auth_shortcut(
        self,
        store: str,
        launcher_path: str,
        title: str,
    ) -> Any:
        """Add auth shortcut."""
        return await _auth.build_auth_shortcut(
            self,
            store,
            launcher_path,
            title,
        )
