"""Launch RPC mixin for Plugin class.

OP-26d | rpc/mixins/launch.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.core.types.events import Events
from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)

_MAX_SAVE_FILES = 500


class LaunchRPCMixin:
    """Game launch notifications, circuit breaker, launch logs, save folders."""

    bus: Any
    config: Any
    services: Any

    def _require_launch_history(self) -> Any:
        svc = getattr(self.services, "launch_history", None)
        if svc is None:
            raise RpcError("service_unavailable", service="launch_history")
        return svc

    async def notify_game_launched(
        self, store: str, game_id: str, **kw: Any,
    ) -> Any:
        await self.bus.emit(
            Events.GAME_LAUNCHED, store=store, game_id=game_id, **kw,
        )

    async def notify_game_stopped(
        self, store: str, game_id: str, exit_code: int = 0,
    ) -> Any:
        await self.bus.emit(
            Events.GAME_STOPPED,
            store=store,
            game_id=game_id,
            exit_code=exit_code,
        )

    async def get_launch_failures(self, game_key: str) -> Any:
        return self._require_launch_history().get_failures(game_key)

    async def clear_launch_failures(self, game_key: str) -> Any:
        return self._require_launch_history().clear_failures(game_key)

    async def arm_circuit_bypass(self, game_key: str) -> Any:
        return self._require_launch_history().arm_bypass(game_key)

    async def get_launch_logs(
        self, launch_id: str, max_lines: int = 500,
    ) -> Any:
        svc = getattr(self.services, "launch_logs", None)
        if svc is None:
            raise RpcError("service_unavailable", service="launch_logs")
        return await svc.read(launch_id, max_lines=max_lines)

    async def export_launch_logs(
        self, launch_id: str, dest_path: str = "",
    ) -> Any:
        svc = getattr(self.services, "launch_logs", None)
        if svc is None:
            raise RpcError("service_unavailable", service="launch_logs")
        return await svc.export(launch_id, dest_path=dest_path)

    async def list_save_folder(
        self,
        store: str,
        game_id: str,
        max_depth: int = 2,
        filter_substring: str = "",
    ) -> Any:
        cloudsave = getattr(self.services, "cloudsave", None)
        if cloudsave is None:
            raise RpcError("service_unavailable", service="cloudsave")
        entries = await cloudsave.list_save_folder(
            store, game_id, max_depth=max_depth,
        )
        if filter_substring:
            entries = [e for e in entries if filter_substring in e.get("name", "")]
        entries.sort(key=lambda e: e.get("size", 0), reverse=True)
        truncated = len(entries) > _MAX_SAVE_FILES
        return {
            "files": entries[:_MAX_SAVE_FILES],
            "truncated": truncated,
        }
