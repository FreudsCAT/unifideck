"""Download RPC handlers.

OP-25c | py_modules/unifideck/rpc/handlers/download.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)


class DownloadHandlers(RpcHandlerBase):
    """Download queue and storage location operations."""

    def _download(self) -> Any:
        """Return the download service, raising if unavailable."""
        return self._require(self._services.download, "download")

    async def cancel_download(self, store: str, game_id: str) -> Any:
        """Cancel an in-progress download."""
        return await self._download().cancel(store, game_id)

    async def get_download_queue(self) -> Any:
        """Return the current download queue."""
        return await self._download().get_queue()

    async def get_storage_locations(self) -> Any:
        """Return available storage locations."""
        storage = getattr(self._services, "storage", None)
        if storage is None:
            return []
        return await storage.get_locations()
