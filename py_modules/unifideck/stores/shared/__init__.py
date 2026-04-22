# OP-47 | stores/shared/__init__.py | Depends: OP-47a, OP-47b
from __future__ import annotations
from .store_registry import StoreRegistry
from .store_base import StoreBase

__all__ = ["StoreRegistry", "StoreBase"]
