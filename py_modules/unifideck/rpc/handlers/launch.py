"""Launch RPC handlers.

OP-25d | py_modules/unifideck/rpc/handlers/launch.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.rpc.errors import RpcError
from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)

_MAX_SAVE_FILES = 500


class LaunchHandlers(RpcHandlerBase):
    """Circuit breaker, launch logs, save folders, and playtime."""

    def _launch_history(self) -> Any:
        return self._require(
            getattr(self._services, "launch_history", None), "launch_history",
        )

    async def get_launch_failures(self, game_key: str) -> Any:
        return self._launch_history().get_failures(game_key)

    async def clear_launch_failures(self, game_key: str) -> Any:
        return self._launch_history().clear_failures(game_key)

    async def arm_circuit_bypass(self, game_key: str) -> Any:
        return self._launch_history().arm_bypass(game_key)

    async def get_launch_logs(self, launch_id: str, max_lines: int = 500) -> Any:
        svc = self._require(
            getattr(self._services, "launch_logs", None), "launch_logs",
        )
        return await svc.read(launch_id, max_lines=max_lines)

    async def export_launch_logs(
        self, launch_id: str, dest_path: str = "",
    ) -> Any:
        svc = self._require(
            getattr(self._services, "launch_logs", None), "launch_logs",
        )
        return await svc.export(launch_id, dest_path=dest_path)

    async def list_save_folder(
        self,
        store: str,
        game_id: str,
        max_depth: int = 2,
        filter_substring: str = "",
    ) -> Any:
        cloudsave = self._require(
            getattr(self._services, "cloudsave", None), "cloudsave",
        )
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

    async def get_playtime(self, store: str, game_id: str) -> Any:
        svc = self._require(
            getattr(self._services, "playtime", None), "playtime",
        )
        return await svc.get(store, game_id)

    async def get_all_playtimes(self) -> Any:
        svc = self._require(
            getattr(self._services, "playtime", None), "playtime",
        )
        return await svc.get_all()
