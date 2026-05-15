"""Store auth RPC handlers.

OP-25g | py_modules/unifideck/rpc/handlers/store.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.rpc.errors import RpcError
from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)


class StoreHandlers(RpcHandlerBase):
    """Store authentication, status, sync, and game operations."""

    async def store_auth(self, store: str, action: str, **kw: Any) -> Any:
        """Delegate auth action to the store registry."""
        return await self._registry.auth_action(store, action, **kw)

    async def check_store_status(self) -> Any:
        """Return availability status of every registered store."""
        result: dict[str, Any] = {}
        for name, adapter in self._registry.all().items():
            try:
                result[name] = {
                    "available": adapter.available,
                    "auth": await adapter.check_auth(),
                }
            except Exception:
                logger.warning("Status check failed for %s", name, exc_info=True)
                result[name] = {"available": False, "auth": False}
        return result

    async def get_store_infos(self) -> Any:
        """Return StoreInfo metadata for every registered store."""
        return self._registry.get_store_infos()

    async def clear_store_auths(self) -> Any:
        """Logout from every store (bulk operation)."""
        return await self._registry.logout_all()

    async def sync_libraries(self, **kw: Any) -> Any:
        """Trigger a full library sync across every store."""
        return await self._sync.sync_all(**kw)

    async def force_sync_libraries(self, **kw: Any) -> Any:
        """Like sync_libraries but bypass the in-progress guard."""
        return await self._sync.sync_all(force=True, **kw)

    async def get_sync_status(self) -> Any:
        """Return whether a sync is running + last completion time."""
        return self._sync.get_status()

    async def get_sync_progress(self) -> Any:
        """Return per-store progress during an in-flight sync."""
        return self._sync.get_progress()

    async def cancel_sync(self) -> Any:
        """Cancel an in-flight sync."""
        return await self._sync.cancel()

    async def get_all_unifideck_games(self) -> Any:
        """Return every known game across every store."""
        return await self._sync.get_all_games()

    async def get_game_info(self, app_id: int) -> Any:
        """Look up a game's info by its Unifideck app_id."""
        return await self._sync.get_game_info(app_id)

    async def install_game(self, store: str, game_id: str, **kw: Any) -> Any:
        """Install a game via the responsible store connector."""
        adapter = self._registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return await adapter.install(game_id, **kw)

    async def uninstall_game(self, store: str, game_id: str) -> Any:
        """Uninstall a game via the responsible store connector."""
        adapter = self._registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return await adapter.uninstall(game_id)

    async def check_game_update(self, store: str, game_id: str) -> Any:
        """Check whether a specific game has an update available."""
        adapter = self._registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return await adapter.check_update(game_id)
