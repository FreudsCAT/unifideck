"""Battle.net store sub-package — public entry point.

py_modules/unifideck/stores/battlenet/__init__.py

Launcher-wrapper store: the vendor Windows client runs inside a per-game
Proton prefix and drives downloading and launching. Discovered by
``StoreRegistry.auto_discover`` via the ``<name>/store.py`` layout, so no
registry edit is needed.
"""

from .store import BattlenetStore

__all__ = ["BattlenetStore"]
