"""Download RPC mixin for Plugin class.

OP-26c | rpc/mixins/download.py
"""
from __future__ import annotations

from typing import Any

from unifideck.rpc.errors import RpcError


class DownloadRPCMixin:
    """Game install/uninstall, download queue, and storage locations."""

    registry: Any
    services: Any

    def _require_store(self, store: str) -> Any:
        adapter = self.registry.get(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return adapter

    def _require_download(self) -> Any:
        svc = getattr(self.services, "download", None)
        if svc is None:
            raise RpcError("service_unavailable", service="download")
        return svc

    async def install_game(self, store: str, game_id: str, **kw: Any) -> Any:
        return await self._require_store(store).install(game_id, **kw)

    async def uninstall_game(self, store: str, game_id: str) -> Any:
        return await self._require_store(store).uninstall(game_id)

    async def check_game_update(self, store: str, game_id: str) -> Any:
        return await self._require_store(store).check_update(game_id)

    async def cancel_download(self, download_id: str) -> Any:
        return await self._require_download().cancel(download_id)

    async def get_download_queue(self) -> Any:
        return await self._require_download().get_queue()

    async def get_storage_locations(self) -> Any:
        storage = getattr(self.services, "storage", None)
        if storage is None:
            return []
        return await storage.get_locations()
