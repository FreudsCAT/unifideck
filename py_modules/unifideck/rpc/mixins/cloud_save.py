"""CloudSaveRPCMixin — manual cloud-save status / pull / push.

Powers the cloud-save icon button next to the custom Play button. Three RPCs:

* ``get_cloud_save_status`` — out-of-band (possibly slow) status: whether the
  game has local/cloud saves, the resolved save path, native cloud support, and
  the auto-pull/auto-push config. Fetched off the render hot path.
* ``cloud_save_pull`` — manual "Download cloud save" → ``sync_down(force=True)``.
  ``force`` overrides the store CLI's "local is newer, skip" so an explicit pull
  always lands (fixes the "can't pull after playing" case).
* ``cloud_save_push`` — manual "Upload local save" → ``sync_up``. Inherits all
  ``safety.py`` wipe-protection guards and the existing conflict-modal flow.

Reaches the live ``CloudSaveService`` via ``self.services.cloudsave`` (the same
pattern ``LaunchRPCMixin.list_save_folder`` uses).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from unifideck.rpc import RpcError

# Strong refs to fire-and-forget sync tasks so the GC can't collect them
# mid-flight (RUF006). The done-callback discards each when it settles.
_SYNC_TASKS: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _SYNC_TASKS.add(task)
    task.add_done_callback(_SYNC_TASKS.discard)


class CloudSaveRPCMixin:
    """Manual cloud-save status + pull/push RPC surface."""

    services: Any
    config: Any

    def _cloudsave(self) -> Any:
        svc = getattr(self.services, "cloudsave", None)
        if svc is None:
            raise RpcError("service_unavailable", service="cloudsave")
        return svc

    async def get_cloud_save_status(self, store: str, game_id: str) -> Any:
        """Return cloud-save status for the button (see module docstring)."""
        return await self._cloudsave().get_cloud_status(store, game_id)

    async def cloud_save_pull(
        self, store: str, game_id: str, force: bool = True,
    ) -> Any:
        """Start a manual cloud→local download. ``force`` defaults True.

        Fire-and-forget: a cloud sync can take many seconds (gogdl/legendary +
        network), which would exceed the RPC client's patience and surface a
        false "failed" even though the sync succeeds. So we kick it off as a
        background task and return immediately — the result is delivered via the
        ``CLOUD_SYNC_DOWN_COMPLETE``/``_FAILED`` events the frontend watches.
        """
        _spawn(self._cloudsave().sync_down(store, game_id, force=force))
        return {"started": True}

    async def cloud_save_push(self, store: str, game_id: str) -> Any:
        """Start a manual local→cloud upload (fire-and-forget; see pull).

        Keeps all safety guards; result arrives via ``CLOUD_SYNC_UP_*`` events.
        """
        _spawn(self._cloudsave().sync_up(store, game_id))
        return {"started": True}

    async def set_game_save_path(
        self, store: str, game_id: str, path: str,
    ) -> Any:
        """Persist a manual save-location override for one game.

        Writes ``games.<game_id>.save_path``, the highest-priority tier in
        ``get_local_save_dir`` — so it wins over all auto-detection from now on.
        Use when auto-detection couldn't find where a game keeps its saves.
        An empty ``path`` clears the override.
        """
        if not store or not game_id:
            raise RpcError("invalid_args", store=store, game_id=game_id)
        cleaned = os.path.expanduser(path.strip()) if path else ""  # noqa: ASYNC240  # pure string op, no I/O — never blocks
        if cleaned and not await asyncio.to_thread(os.path.isdir, cleaned):
            raise RpcError("path_not_found", path=cleaned)
        self.config.set(f"games.{game_id}.save_path", cleaned)
        return {"success": True, "store": store, "game_id": game_id, "save_path": cleaned}
