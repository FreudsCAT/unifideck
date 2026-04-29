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
        return await self._registry.auth_action(store, action, **kw)

    async def check_store_status(self) -> Any:
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
        return self._registry.get_store_infos()

    async def clear_store_auths(self) -> Any:
        return await self._registry.logout_all()

    async def sync_libraries(self, **kw: Any) -> Any:
        return await self._sync.sync(**kw)

    async def force_sync_libraries(self, **kw: Any) -> Any:
        return await self._sync.sync(force=True, **kw)

    async def get_sync_status(self) -> Any:
        return self._sync.get_status()

    async def get_sync_progress(self) -> Any:
        return self._sync.get_progress()

    async def cancel_sync(self) -> Any:
        return await self._sync.cancel()

    async def get_all_unifideck_games(self) -> Any:
        return await self._sync.get_all_games()

    async def get_game_info(self, app_id: int) -> Any:
        return await self._sync.get_game_info(app_id)

    async def install_game(self, store: str, game_id: str, **kw: Any) -> Any:
        adapter = self._registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return await adapter.install(game_id, **kw)

    async def uninstall_game(self, store: str, game_id: str) -> Any:
        adapter = self._registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return await adapter.uninstall(game_id)

    async def check_game_update(self, store: str, game_id: str) -> Any:
        adapter = self._registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return await adapter.check_update(game_id)
