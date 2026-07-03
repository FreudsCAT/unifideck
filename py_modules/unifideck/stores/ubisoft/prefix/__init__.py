"""
Prefix sub-package — public exports.

OP-59 | py_modules/unifideck/stores/ubisoft/prefix/__init__.py

Re-exports ``UbisoftPrefixManager``, the orchestration class for Wine
prefix lifecycle: create, template, mount, validate, remove.
"""

from .manager import UbisoftPrefixManager

__all__ = ["UbisoftPrefixManager"]
