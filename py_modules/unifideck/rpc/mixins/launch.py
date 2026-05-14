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
        """Return LaunchHistoryService or raise ``service_unavailable``."""
        svc = getattr(self.services, "launch_history", None)
        if svc is None:
            raise RpcError("service_unavailable", service="launch_history")
        return svc

    async def notify_game_launched(
        self, store: str, game_id: str, **kw: Any,
    ) -> Any:
        """Emit GAME_LAUNCHED event to the bus."""
        await self.bus.emit(
            Events.GAME_LAUNCHED, store=store, game_id=game_id, **kw,
        )

    async def notify_game_stopped(
        self, store: str, game_id: str, exit_code: int = 0,
    ) -> Any:
        """Emit GAME_STOPPED event with exit code."""
        await self.bus.emit(
            Events.GAME_STOPPED,
            store=store,
            game_id=game_id,
            exit_code=exit_code,
        )

    async def get_launch_failures(self, game_key: str) -> Any:
        """Return recent failures + circuit state for a game.

        Bundles two service methods (``get_recent_failures`` and
        ``is_circuit_open``) into one RPC payload — neither exists
        as ``get_failures`` on :class:`LaunchHistoryService`, so the
        previous version raised ``AttributeError``.
        """
        svc = self._require_launch_history()
        is_open, fail_count = svc.is_circuit_open(game_key)
        return {
            "failures": svc.get_recent_failures(game_key),
            "circuit_open": is_open,
            "fail_count": fail_count,
        }

    async def clear_launch_failures(self, game_key: str) -> Any:
        """Wipe failure history for one game (full reset)."""
        return self._require_launch_history().clear_failures(game_key)

    async def arm_circuit_bypass(self, game_key: str) -> Any:
        """Arm a one-shot bypass flag (5-minute validity)."""
        return self._require_launch_history().arm_bypass(game_key)

    async def get_launch_logs(
        self, launch_id: str, max_lines: int = 500,
    ) -> Any:
        """Tail the log file for a specific launch id."""
        svc = getattr(self.services, "launch_logs", None)
        if svc is None:
            raise RpcError("service_unavailable", service="launch_logs")
        return await svc.read(launch_id, max_lines=max_lines)

    async def export_launch_logs(
        self, launch_id: str, dest_path: str = "",
    ) -> Any:
        """Copy archived logs to ``dest_path``."""
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
        """Return contents of a game's local cloud save folder."""
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
