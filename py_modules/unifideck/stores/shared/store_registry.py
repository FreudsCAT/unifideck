"""stores/shared/store_registry.py — Runtime store dispatcher.
# OP-47a | stores/shared/store_registry.py | Depends: (none)

Replaces 109 per-store if/elif chains with a dict-lookup registry.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store_base import StoreBase


class StoreRegistry:
    """Dict-based dispatcher: register once, iterate generically."""

    def __init__(self) -> None:
        raise NotImplementedError("OP-47a: init empty _stores dict")

    def register(self, store_id: str, store: StoreBase) -> None:
        """Register a store. Emits STORE_REGISTERED on bus."""
        raise NotImplementedError("OP-47a: _stores[store_id] = store, emit event")

    def get(self, store_id: str) -> StoreBase:
        """Get store by ID. Raises KeyError if not registered."""
        raise NotImplementedError("OP-47a: return _stores[store_id]")

    def all(self) -> list[StoreBase]:
        """Return all registered stores."""
        raise NotImplementedError("OP-47a: return list(_stores.values())")

    def available(self) -> list[StoreBase]:
        """Return only stores where is_available() is True."""
        raise NotImplementedError("OP-47a: filter by is_available()")

    async def auth_action(self, store_id: str, action: str, **kwargs) -> dict:
        """Dispatch auth action to a specific store."""
        raise NotImplementedError("OP-47a: get(store_id).{action}_auth(**kwargs)")
