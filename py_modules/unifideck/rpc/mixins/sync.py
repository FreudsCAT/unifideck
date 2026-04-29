"""Sync RPC mixin for Plugin class.

OP-26f | rpc/mixins/sync.py
"""
from __future__ import annotations

from typing import Any


class SyncRPCMixin:
    """Library sync, progress, and game queries."""

    sync_service: Any

    async def sync_libraries(self, **kw: Any) -> Any:
        return await self.sync_service.sync(**kw)

    async def force_sync_libraries(self, **kw: Any) -> Any:
        return await self.sync_service.sync(force=True, **kw)

    async def get_sync_status(self) -> Any:
        return self.sync_service.get_status()

    async def get_sync_progress(self) -> Any:
        return self.sync_service.get_progress()

    async def cancel_sync(self) -> Any:
        return await self.sync_service.cancel()

    async def get_all_unifideck_games(self) -> Any:
        return await self.sync_service.get_all_games()

    async def get_game_info(self, app_id: int) -> Any:
        return await self.sync_service.get_game_info(app_id)
