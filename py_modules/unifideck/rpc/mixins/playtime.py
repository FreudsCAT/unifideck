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
        """Return playtime data for a specific game.

        Real method is :meth:`PlaytimeService.get_playtime` (see
        handler twin for the rationale).
        """
        return await self._require_playtime().get_playtime(store, game_id)

    async def get_all_playtimes(self) -> Any:
        """Return playtime data for every game with sessions."""
        return await self._require_playtime().get_all_playtimes()

    async def sync_playtime_now(self) -> Any:
        """Force a playtime → store drain now. Returns ``{store: pushed}``.

        Sync is otherwise automatic (on every session end + at startup); this
        is a manual/debug trigger. No-op-safe if the service is unavailable.
        """
        svc = getattr(self.services, "playtime_sync", None)
        if svc is None:
            raise RpcError("service_unavailable", service="playtime_sync")
        return await svc.sync_now()
