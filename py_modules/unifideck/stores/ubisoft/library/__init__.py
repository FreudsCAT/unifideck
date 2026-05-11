"""
Library sub-package — public exports.

OP-57 | py_modules/unifideck/stores/ubisoft/library/__init__.py

Re-exports ``UbisoftLibrary``, the orchestration class for everything
related to the user's Ubisoft game library: list owned games, detect
installed ones, build display metadata, merge with the Steam shadow.
"""

from .facade import UbisoftLibrary

__all__ = ["UbisoftLibrary"]
