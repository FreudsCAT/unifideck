"""Playtime RPC mixin for Plugin class.

OP-26j | rpc/mixins/playtime.py
"""
from __future__ import annotations

from typing import Any

from unifideck.rpc.errors import RpcError


class PlaytimeRPCMixin:
    """Per-game and aggregate playtime queries."""

    services: Any

    def _require_playtime(self) -> Any:
        """Return PlaytimeService or raise ``service_unavailable``."""
        svc = getattr(self.services, "playtime", None)
        if svc is None:
            raise RpcError("service_unavailable", service="playtime")
        return svc

    async def get_playtime(self, store: str, game_id: str) -> Any:
        """Return playtime data for a specific game."""
        return await self._require_playtime().get(store, game_id)

    async def get_all_playtimes(self) -> Any:
        """Return playtime data for every game with sessions."""
        return await self._require_playtime().get_all()
