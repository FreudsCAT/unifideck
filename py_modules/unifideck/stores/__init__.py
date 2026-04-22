# OP-46 | stores/__init__.py | Depends: OP-47a
from __future__ import annotations
from .shared.store_registry import StoreRegistry

__all__ = ["StoreRegistry"]
