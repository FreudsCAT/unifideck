"""services/cloud_save/sync.py — sync_down / sync_up / resolve_conflict.

3 async methods driving bidirectional sync around launches.
All three gate on a non-empty ``_cloud_root`` (no-op success
when cloud sync is disabled) and coordinate via ``_syncing`` —
per-game ``asyncio.Event`` dict that serialises overlapping
down/up pairs for the same key.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import TYPE_CHECKING, Any

from ...core.types import Result
from .manifest import read_manifest, write_manifest

if TYPE_CHECKING:
    from ...event_bus.event_bus import EventBus
    # This is a mixin; `self` will be the CloudSaveService facade at runtime.
    pass

logger = logging.getLogger(__name__)


class _SyncMixin:
    """Bidirectional cloud sync methods for CloudSaveService."""

    # Provided by the CloudSaveService facade at runtime
    _bus: EventBus
    _cloud_root: str | None
    _local_root: str
    _syncing: dict[str, asyncio.Event]
    _tolerance: float
    _sync_wait_timeout: float

    async def sync_down(self: Any, store: str, game_id: str) -> Result:
        """Pull the cloud save before a game launch.

        No-op success when ``_cloud_root`` is unset. Acquires the
        per-game event, reads remote + local manifests, compares
        mtimes (with ``_tolerance`` slack), detects conflict
        (both sides modified since last sync) → returns
        ``sync_conflict`` for operator resolution. Otherwise
        copies remote → local atomically via ``copy_tree`` and
        updates the local manifest. Emits
        ``CLOUD_SYNC_DOWN_{COMPLETE,FAILED}``.
        """
        if not self._cloud_root:
            return Result(success=True)

        key = f"{store}:{game_id}"
        from ...core.types.events import Events

        if key not in self._syncing:
            self._syncing[key] = asyncio.Event()
            self._syncing[key].set()

        # Wait for any in-flight syncs for this game
        try:
            await asyncio.wait_for(self._syncing[key].wait(), timeout=self._sync_wait_timeout)
        except asyncio.TimeoutError:
            error = "sync_wait_timeout"
            if self._bus:
                self._bus.emit(Events.CLOUD_SYNC_DOWN_FAILED, store=store, game_id=game_id, error=error)
            return Result(success=False, error=error)

        self._syncing[key].clear()

        try:
            local_dir = self.get_local_save_dir(store, game_id)
            remote_dir = os.path.join(self._cloud_root, store, game_id)

            if not os.path.isdir(remote_dir):
                # Nothing to sync down
                if self._bus:
                    self._bus.emit(Events.CLOUD_SYNC_DOWN_COMPLETE, store=store, game_id=game_id, synced=False)
                return Result(success=True)

            local_manifest = await read_manifest(local_dir)
            remote_manifest = await read_manifest(remote_dir)

            # Check for conflict: both sides modified since last sync
            # To detect this accurately, we'd look at file mtimes vs manifest mtimes.
            # For simplicity, if local has changes not in the manifest AND remote has changes not in manifest, conflict.
            # In Unifideck, we compare local mtimes against the remote manifest.
            # If local files are newer than remote manifest (with tolerance), and remote files are newer than local manifest.
            local_modified = await self._is_modified(local_dir, remote_manifest)
            remote_modified = await self._is_modified(remote_dir, local_manifest)

            if local_modified and remote_modified:
                error = "sync_conflict"
                if self._bus:
                    self._bus.emit(Events.CLOUD_SYNC_DOWN_FAILED, store=store, game_id=game_id, error=error)
                return Result(success=False, error=error)

            if not remote_modified:
                # Remote hasn't changed, no need to pull
                if self._bus:
                    self._bus.emit(Events.CLOUD_SYNC_DOWN_COMPLETE, store=store, game_id=game_id, synced=False)
                return Result(success=True)

            # Do the copy: Remote -> Local
            await self._copy_tree(remote_dir, local_dir)

            # Update local manifest to match remote
            new_manifest = await self._build_manifest(local_dir)
            await write_manifest(local_dir, new_manifest)

            if self._bus:
                self._bus.emit(Events.CLOUD_SYNC_DOWN_COMPLETE, store=store, game_id=game_id, synced=True)
            return Result(success=True)

        except Exception as e:
            logger.error("[CloudSaveService] sync_down failed for %s: %s", key, e)
            if self._bus:
                self._bus.emit(Events.CLOUD_SYNC_DOWN_FAILED, store=store, game_id=game_id, error=str(e))
            return Result(success=False, error=str(e))
        finally:
            self._syncing[key].set()

    async def sync_up(self: Any, store: str, game_id: str) -> Result:
        """Push the local save to the cloud after the game exits.

        No-op success when ``_cloud_root`` is unset. Waits briefly
        for any in-flight sync_down to finish (``_sync_wait_timeout``
        bound). Reads local manifest, copies local → remote via
        ``copy_tree``, writes the fresh manifest. Emits
        ``CLOUD_SYNC_UP_{COMPLETE,FAILED}``.
        """
        if not self._cloud_root:
            return Result(success=True)

        key = f"{store}:{game_id}"
        from ...core.types.events import Events

        if key not in self._syncing:
            self._syncing[key] = asyncio.Event()
            self._syncing[key].set()

        try:
            await asyncio.wait_for(self._syncing[key].wait(), timeout=self._sync_wait_timeout)
        except asyncio.TimeoutError:
            error = "sync_wait_timeout"
            if self._bus:
                self._bus.emit(Events.CLOUD_SYNC_UP_FAILED, store=store, game_id=game_id, error=error)
            return Result(success=False, error=error)

        self._syncing[key].clear()

        try:
            local_dir = self.get_local_save_dir(store, game_id)
            remote_dir = os.path.join(self._cloud_root, store, game_id)

            if not os.path.isdir(local_dir):
                if self._bus:
                    self._bus.emit(Events.CLOUD_SYNC_UP_COMPLETE, store=store, game_id=game_id, synced=False)
                return Result(success=True)

            remote_manifest = await read_manifest(remote_dir)

            local_modified = await self._is_modified(local_dir, remote_manifest)

            if not local_modified:
                if self._bus:
                    self._bus.emit(Events.CLOUD_SYNC_UP_COMPLETE, store=store, game_id=game_id, synced=False)
                return Result(success=True)

            # Do the copy: Local -> Remote
            await self._copy_tree(local_dir, remote_dir)

            # Update remote manifest
            new_manifest = await self._build_manifest(remote_dir)
            await write_manifest(remote_dir, new_manifest)
            # Also update local so they match exactly
            await write_manifest(local_dir, new_manifest)

            if self._bus:
                self._bus.emit(Events.CLOUD_SYNC_UP_COMPLETE, store=store, game_id=game_id, synced=True)
            return Result(success=True)

        except Exception as e:
            logger.error("[CloudSaveService] sync_up failed for %s: %s", key, e)
            if self._bus:
                self._bus.emit(Events.CLOUD_SYNC_UP_FAILED, store=store, game_id=game_id, error=str(e))
            return Result(success=False, error=str(e))
        finally:
            self._syncing[key].set()

    async def resolve_conflict(self: Any, store: str, game_id: str, choice: str) -> Result:
        """Resolve a ``sync_conflict`` with ``choice`` in {local, remote}.

        ``local`` → force-push local as the new canonical (overwrite
        remote). ``remote`` → force-pull remote as canonical
        (overwrite local). Any other value → Result(success=False,
        error="invalid_choice"). Writes a fresh manifest at the end
        so the next sync sees no conflict.
        """
        if not self._cloud_root:
            return Result(success=True)

        if choice not in ("local", "remote"):
            return Result(success=False, error="invalid_choice")

        key = f"{store}:{game_id}"
        if key not in self._syncing:
            self._syncing[key] = asyncio.Event()
            self._syncing[key].set()

        try:
            await asyncio.wait_for(self._syncing[key].wait(), timeout=self._sync_wait_timeout)
        except asyncio.TimeoutError:
            return Result(success=False, error="sync_wait_timeout")

        self._syncing[key].clear()

        try:
            local_dir = self.get_local_save_dir(store, game_id)
            remote_dir = os.path.join(self._cloud_root, store, game_id)

            if choice == "local":
                # Push local to remote
                await self._copy_tree(local_dir, remote_dir)
                new_manifest = await self._build_manifest(local_dir)
                await write_manifest(remote_dir, new_manifest)
                await write_manifest(local_dir, new_manifest)
            elif choice == "remote":
                # Pull remote to local
                await self._copy_tree(remote_dir, local_dir)
                new_manifest = await self._build_manifest(remote_dir)
                await write_manifest(local_dir, new_manifest)
                await write_manifest(remote_dir, new_manifest)

            return Result(success=True)

        except Exception as e:
            logger.error("[CloudSaveService] resolve_conflict failed for %s: %s", key, e)
            return Result(success=False, error=str(e))
        finally:
            self._syncing[key].set()

    # --- Private Helpers ---

    async def _is_modified(self, directory: str, manifest: dict[str, float]) -> bool:
        """Check if any file in `directory` differs from `manifest` mtimes."""
        def _check_sync() -> bool:
            if not os.path.exists(directory):
                return False

            current = {}
            for root, _, files in os.walk(directory):
                for f in files:
                    # Ignore the manifest file itself
                    if f == ".unifideck_sync.json":
                        continue
                    path = os.path.join(root, f)
                    rel = os.path.relpath(path, directory)
                    try:
                        current[rel] = os.path.getmtime(path)
                    except OSError:
                        pass

            # If sets of files differ
            if set(current.keys()) != set(manifest.keys()):
                return True

            # If any mtime drifted beyond tolerance
            for rel, mtime in current.items():
                if abs(mtime - manifest.get(rel, 0.0)) > getattr(self, "_tolerance", 2.0):
                    return True

            return False

        return await asyncio.to_thread(_check_sync)

    async def _build_manifest(self, directory: str) -> dict[str, float]:
        """Build a fresh manifest dict of rel_path -> mtime."""
        def _build_sync() -> dict[str, float]:
            manifest = {}
            if not os.path.exists(directory):
                return manifest

            for root, _, files in os.walk(directory):
                for f in files:
                    if f == ".unifideck_sync.json":
                        continue
                    path = os.path.join(root, f)
                    rel = os.path.relpath(path, directory)
                    try:
                        manifest[rel] = os.path.getmtime(path)
                    except OSError:
                        pass
            return manifest

        return await asyncio.to_thread(_build_sync)

    async def _copy_tree(self, src: str, dst: str) -> None:
        """Copy src directory to dst atomically (via tmp)."""
        def _copy_sync() -> None:
            if not os.path.exists(src):
                return
            
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
                
            tmp_dst = dst + ".tmp"
            if os.path.exists(tmp_dst):
                shutil.rmtree(tmp_dst)
                
            shutil.copytree(src, tmp_dst, dirs_exist_ok=True)
            
            # Atomic swap
            if os.path.exists(dst):
                # os.replace requires destination to be empty if it's a directory
                # But we can just remove the old one first, or move it away.
                backup_dst = dst + ".bak"
                if os.path.exists(backup_dst):
                    shutil.rmtree(backup_dst)
                os.rename(dst, backup_dst)
                os.rename(tmp_dst, dst)
                shutil.rmtree(backup_dst)
            else:
                os.rename(tmp_dst, dst)

        await asyncio.to_thread(_copy_sync)
