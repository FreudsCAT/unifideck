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
    """Direct read/write access to the shortcuts.vdf data structure."""

    _shortcuts: list[dict[str, Any]]

    async def read_shortcuts(self) -> dict[str, Any]:
        """Return the shortcuts.vdf contents in its on-disk shape.

        Steam's VDF format stores the shortcut list as a dict
        keyed by stringified indices (``"0"``, ``"1"``, …) rather
        than as a JSON-style array. This method converts our
        in-memory list back to that shape so callers (RPC layer)
        that need to pass the data straight to Steam don't have
        to transform it themselves.

        Returns:
            ``{"shortcuts": {"0": entry, "1": entry, …}}``.
        """
        await self._load_shortcuts()
        return {
            "shortcuts": {str(i): entry for i, entry in enumerate(self._shortcuts)},
        }

    async def write_shortcuts(self, data: dict[str, Any]) -> None:
        """Replace the in-memory shortcuts list and persist.

        Used by the RPC layer when the user (or another tool)
        rewrites shortcuts.vdf wholesale. Bypasses the per-game
        CRUD methods — useful for restoring a backup, but caller
        beware: no validation of the dict's shape is performed.

        Args:
            data: ``{"shortcuts": {…}}`` dict; the values are
                taken as the new shortcuts list (key order is
                ignored, dict insertion order is preserved).
        """
        shortcuts_map = data.get("shortcuts", {})
        self._shortcuts = list(shortcuts_map.values())
        await self._save_all()

    async def add_auth_shortcut(
        self,
        store: str,
        launcher_path: str,
        title: str,
    ) -> Any:
        """Create a Steam shortcut for a store's auth launcher.

        Delegates to ``auth_shortcut.build_auth_shortcut`` which
        builds the per-store shortcut (Ubisoft Connect, Epic
        Games Launcher, etc.) used to drive the embedded auth
        flow.

        Args:
            store: store identifier.
            launcher_path: absolute path to the launcher
                executable.
            title: display title shown in the Steam library.

        Returns:
            ``Result`` from ``build_auth_shortcut``.
        """
        return await _auth.build_auth_shortcut(
            self,
            store,
            launcher_path,
            title,
        )
