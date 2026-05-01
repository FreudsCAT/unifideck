"""Store RPC mixin for Plugin class.

OP-26e | rpc/mixins/store.py
"""
from __future__ import annotations

from typing import Any


class StoreRPCMixin:
    """Store authentication and status operations."""

    registry: Any

    async def store_auth(self, store: str, action: str, **kw: Any) -> Any:
        """Delegate auth action to the store registry."""
        return await self.registry.auth_action(store, action, **kw)

    async def check_store_status(self) -> Any:
        """Return availability status of every registered store."""
        return await self.registry.check_all_status()

    async def get_store_infos(self) -> Any:
        """Return StoreInfo metadata for every registered store."""
        return self.registry.get_store_infos()

    async def clear_store_auths(self) -> Any:
        """Logout from every store (bulk operation)."""
        return await self.registry.logout_all()
