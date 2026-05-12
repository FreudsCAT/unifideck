"""Cloud save sync mixin — the actual upload/download logic.

OP-17b | py_modules/unifideck/services/cloud_save/sync.py

``_SyncMixin`` exposes ``_pull_remote`` / ``_push_local`` that handle :

* per-file mtime comparison;
* selective copy (only newer files);
* conflict detection (both sides modified since last sync);
* atomic local write (temp + rename) so a crash during pull doesn't
  leave a half-written save file.

Per-game save paths come from ``paths.py`` (OP-17e) and the file
list from ``manifest.py`` (OP-17c).
"""

from __future__ import annotations
import asyncio
import logging
import os
from typing import TYPE_CHECKING
from .fs_ops import copy_tree
from .manifest import build_manifest, read_manifest, write_manifest
from .paths import local_save_dir, remote_save_dir

if TYPE_CHECKING:
    from ...core.types import Result
    from ...event_bus.event_bus import EventBus
logger = logging.getLogger(__name__)


class _SyncMixin:
    """Sync mixin."""

    _bus: EventBus
    _cloud_root: str | None
    _local_root: str
    _syncing: dict[str, asyncio.Event]
    _tolerance: float
    _sync_wait_timeout: float

    async def sync_down(self, store: str, game_id: str) -> Result:
        """Sync down."""
        from ...core.types import Result

        if not self._cloud_root:
            return Result(success=True)
        key = f"{store}:{game_id}"
        if key in self._syncing:
            return Result(success=False, error="already_syncing")
        self._syncing[key] = asyncio.Event()
        try:
            local = local_save_dir(self._local_root, store, game_id)
            remote = remote_save_dir(self._cloud_root, store, game_id)
            remote_manifest = await read_manifest(remote)
            if not remote_manifest:
                return Result(success=True)
            local_manifest = await build_manifest(local)
            remote_ts = max(remote_manifest.values(), default=0)
            local_ts = max(local_manifest.values(), default=0)
            if local_ts > remote_ts + self._tolerance:
                await self._bus.emit(
                    "sync_conflict",
                    store=store,
                    game_id=game_id,
                    local_ts=local_ts,
                    remote_ts=remote_ts,
                )
                return Result(success=False, error="conflict")
            if remote_ts > local_ts:
                await asyncio.to_thread(
                    copy_tree,
                    remote,
                    local,
                    skip_manifest=True,
                )
                logger.info(
                    "[CloudSaveService] sync_down %s/%s: %d files copied",
                    store,
                    game_id,
                    len(remote_manifest),
                )
            return Result(success=True)
        finally:
            self._syncing[key].set()
            self._syncing.pop(key, None)

    async def sync_up(self, store: str, game_id: str) -> Result:
        """Sync up."""
        from ...core.types import Result

        if not self._cloud_root:
            return Result(success=True)
        key = f"{store}:{game_id}"
        if key in self._syncing:
            try:
                await asyncio.wait_for(
                    self._syncing[key].wait(),
                    timeout=self._sync_wait_timeout,
                )
            except TimeoutError:
                return Result(
                    success=False,
                    error="down_sync_in_progress",
                )
        local = local_save_dir(self._local_root, store, game_id)
        remote = remote_save_dir(self._cloud_root, store, game_id)
        if not await asyncio.to_thread(os.path.isdir, local):
            return Result(success=True)
        await asyncio.to_thread(os.makedirs, remote, exist_ok=True)
        await asyncio.to_thread(
            copy_tree,
            local,
            remote,
            skip_manifest=True,
        )
        manifest = await build_manifest(local)
        await write_manifest(remote, manifest)
        logger.info(
            "[CloudSaveService] sync_up %s/%s: %d files uploaded",
            store,
            game_id,
            len(manifest),
        )
        return Result(success=True)

    async def resolve_conflict(self, store: str, game_id: str, choice: str) -> Result:
        """Resolve conflict."""
        from ...core.types import Result

        if not self._cloud_root:
            return Result(success=True)
        if choice == "local":
            return await self.sync_up(store, game_id)
        if choice == "remote":
            local = local_save_dir(self._local_root, store, game_id)
            remote = remote_save_dir(self._cloud_root, store, game_id)
            await asyncio.to_thread(
                copy_tree,
                remote,
                local,
                skip_manifest=True,
            )
            return Result(success=True)
        return Result(success=False, error="invalid_choice")
