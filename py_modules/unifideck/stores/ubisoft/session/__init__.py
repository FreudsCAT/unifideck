"""
Session sub-package — public exports.

OP-60 | py_modules/unifideck/stores/ubisoft/session/__init__.py

Re-exports ``UbisoftSession``, the orchestration class for UPC session
state propagation between Wine prefixes.
"""

from .facade import UbisoftSession

__all__ = ["UbisoftSession"]
