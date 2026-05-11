"""Install sub-package — exposes nothing publicly.

OP-51 | py_modules/unifideck/stores/gog/install/__init__.py

This ``__init__`` is intentionally empty. Install components are
internal to the GOG sub-package — callers reach them through
``GOGInstaller`` exposed by ``store.py`` (OP-50a).
"""

from .installer import GOGInstaller

__all__ = ["GOGInstaller"]
