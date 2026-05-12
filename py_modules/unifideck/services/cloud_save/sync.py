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
    """Bidirectional save sync — pull / push / conflict resolution.

    Stateful mixin: relies on attributes set by the host class
    (``CloudSaveService``) — ``_bus``, ``_cloud_root``, ``_local_root``,
    ``_syncing``, ``_tolerance``, ``_sync_wait_timeout``.

    The mixin is responsible for the actual mtime comparison and
    file copy ; the host class wires it to bus events. Both
    directions use the same ``copy_tree`` primitive but with
    different sources / targets.
    """

    _bus: EventBus
    _cloud_root: str | None
    _local_root: str
    _syncing: dict[str, asyncio.Event]
    _tolerance: float
    _sync_wait_timeout: float

    async def sync_down(self, store: str, game_id: str) -> Result:
        """Pull the cloud copy into the local save dir if fresher.

        Workflow:

        1. Mark the game as "in-flight" via ``self._syncing`` so a
           concurrent ``sync_up`` will wait.
        2. Read the cloud-side manifest. If absent → nothing to
           pull, treat as success.
        3. Build the local manifest on the fly.
        4. Compare the two latest mtimes; if local is fresher than
           remote (beyond the tolerance), it's a conflict — emit
           ``sync_conflict`` and refuse the pull.
        5. If remote is fresher, copy the cloud tree into the
           local directory (skipping the manifest file itself).

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``Result(success=True)`` on successful sync or no-op,
            ``Result(success=False, error="conflict")`` when the
            local copy is fresher, ``Result(success=False,
            error="already_syncing")`` if another sync is in
            progress on the same key.
        """
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
        """Push the local save dir to the cloud root.

        Unlike ``sync_down`` this method doesn't compare mtimes —
        it unconditionally copies local → cloud. The rationale: the
        method is only called via ``GAME_STOPPED`` after the game
        has just been writing saves locally, so the local copy is
        by construction the freshest one.

        If a ``sync_down`` is in flight for the same key, this
        method waits for it (up to ``_sync_wait_timeout``) before
        starting the push, to avoid pushing a half-pulled state.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``Result(success=True)`` on successful push or no-op
            (no local dir to push from),
            ``Result(success=False, error="down_sync_in_progress")``
            if the wait timed out.
        """
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
        """Apply a user-chosen resolution for a previously-detected conflict.

        Called from the RPC layer when the user clicks one of the
        two conflict-resolution buttons:

        * ``"local"``  — accept the local copy, push it cloud-ward
          (delegates to ``sync_up``);
        * ``"remote"`` — accept the cloud copy, pull it locally
          (forced copy, no mtime check).

        Args:
            store: store identifier.
            game_id: store-specific game id.
            choice: ``"local"`` or ``"remote"`` — anything else
                returns ``invalid_choice``.

        Returns:
            ``Result(success=True)`` on a successful resolution,
            ``Result(success=False, error="invalid_choice")`` if
            ``choice`` is not one of the accepted values.
        """
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
