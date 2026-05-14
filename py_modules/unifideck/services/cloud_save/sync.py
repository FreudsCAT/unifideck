"""services/cloud_save/sync.py — sync_down / sync_up / resolve_conflict.

3 async methods driving bidirectional sync around launches.
All three gate on a non-empty ``_cloud_root`` (no-op success
when cloud sync is disabled) and coordinate via ``_syncing`` —
a per-game ``asyncio.Lock`` dict that serialises overlapping
down/up pairs for the same key. Lock acquisition is atomic
(``async with``) and bounded by ``_sync_wait_timeout`` to
match the previous Event-based contract.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Result

from .manifest import read_manifest, write_manifest

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus
    # This is a mixin; `self` will be the CloudSaveService facade at runtime.

logger = logging.getLogger(__name__)


def _get_lock(syncing: dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
    """Return the per-key lock, creating it on first access.

    Lock creation is itself protected by an internal-to-asyncio
    invariant: even if two coroutines race into ``setdefault`` they
    both end up referring to the same Lock instance, so subsequent
    ``async with`` calls serialise correctly.
    """
    return syncing.setdefault(key, asyncio.Lock())


class _SyncMixin:
    """Bidirectional cloud sync methods for CloudSaveService."""

    # Provided by the CloudSaveService facade at runtime
    _bus: EventBus
    _cloud_root: str | None
    _local_root: str
    _syncing: dict[str, asyncio.Lock]
    _tolerance: float
    _sync_wait_timeout: float

    async def _emit_down(
        self: Any,
        event: str,
        store: str,
        game_id: str,
        **payload: Any,
    ) -> None:
        """Fire one of the ``CLOUD_SYNC_DOWN_*`` events if a bus exists.

        Centralised so the per-method emit sites stay short and
        consistent. The ``if self._bus`` guard keeps the service
        usable in tests where no real event bus is wired.

        ``EventBus.emit`` is ``async`` (it awaits every subscriber
        in turn), so this helper is also ``async`` and every call
        site must ``await`` it. An earlier version was sync and
        silently dropped the returned coroutine — none of the
        ``CLOUD_SYNC_*`` events ever fired and the UI's sync
        indicator was a no-op.
        """
        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(getattr(Events, event), store=store, game_id=game_id, **payload)

    async def _emit_up(
        self: Any,
        event: str,
        store: str,
        game_id: str,
        **payload: Any,
    ) -> None:
        """Counterpart of :meth:`_emit_down` for the ``CLOUD_SYNC_UP_*`` events."""
        if self._bus:
            from unifideck.core.types.events import Events
            await self._bus.emit(getattr(Events, event), store=store, game_id=game_id, **payload)

    async def _acquire_sync_lock(
        self: Any,
        key: str,
        on_timeout_event: str,
        store: str,
        game_id: str,
    ) -> tuple[asyncio.Lock | None, Result | None]:
        """Acquire the per-game lock with the configured wait timeout.

        On success returns ``(lock, None)`` and the caller must
        release the lock in its ``finally:``. On timeout returns
        ``(None, Result(...))`` with the right canonical error
        code and emits the matching ``*_FAILED`` event. Splitting
        this off keeps ``sync_down``/``sync_up`` focused on the
        actual sync logic.
        """
        lock = _get_lock(self._syncing, key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._sync_wait_timeout)
        except asyncio.TimeoutError:
            error = "sync_wait_timeout"
            emit = self._emit_down if on_timeout_event.startswith("CLOUD_SYNC_DOWN") else self._emit_up
            await emit(on_timeout_event, store, game_id, error=error)
            return None, Result(success=False, error=error)
        return lock, None

    async def _sync_down_locked(
        self: Any,
        store: str,
        game_id: str,
        key: str,
    ) -> Result:
        """Inner sync_down body — runs with the per-game lock held.

        Pre-flight: bail out if there's no remote dir to pull from.
        Read both manifests, detect conflict (both sides newer than
        the last shared manifest), bail out if remote is unchanged,
        otherwise copy remote → local and write the fresh manifest.
        """
        local_dir = self.get_local_save_dir(store, game_id)
        remote_dir = str(Path(self._cloud_root) / store, game_id)

        if not await asyncio.to_thread(lambda: Path(remote_dir).is_dir()):
            await self._emit_down("CLOUD_SYNC_DOWN_COMPLETE", store, game_id, synced=False)
            return Result(success=True)

        local_manifest = await read_manifest(local_dir)
        remote_manifest = await read_manifest(remote_dir)

        local_modified = await self._is_modified(local_dir, remote_manifest)
        remote_modified = await self._is_modified(remote_dir, local_manifest)

        if local_modified and remote_modified:
            error = "sync_conflict"
            await self._emit_down("CLOUD_SYNC_DOWN_FAILED", store, game_id, error=error)
            return Result(success=False, error=error)

        if not remote_modified:
            await self._emit_down("CLOUD_SYNC_DOWN_COMPLETE", store, game_id, synced=False)
            return Result(success=True)

        await self._copy_tree(remote_dir, local_dir)
        new_manifest = await self._build_manifest(local_dir)
        await write_manifest(local_dir, new_manifest)

        await self._emit_down("CLOUD_SYNC_DOWN_COMPLETE", store, game_id, synced=True)
        return Result(success=True)

    async def sync_down(self: Any, store: str, game_id: str) -> Result:
        """Pull the cloud save before a game launch.

        No-op success when ``_cloud_root`` is unset. Acquires the
        per-game ``asyncio.Lock`` (atomic, bounded by
        ``_sync_wait_timeout``), then delegates to
        :meth:`_sync_down_locked` for the actual work. Lock
        release is guaranteed by the ``finally:`` clause.
        """
        if not self._cloud_root:
            return Result(success=True)

        key = f"{store}:{game_id}"
        lock, timeout_result = await self._acquire_sync_lock(
            key, "CLOUD_SYNC_DOWN_FAILED", store, game_id,
        )
        if lock is None:
            return timeout_result  # type: ignore[return-value]

        try:
            return await self._sync_down_locked(store, game_id, key)
        except Exception as e:
            logger.exception("[CloudSaveService] sync_down failed for %s", key)
            await self._emit_down("CLOUD_SYNC_DOWN_FAILED", store, game_id, error=str(e))
            return Result(success=False, error=str(e))
        finally:
            lock.release()

    async def _sync_up_locked(
        self: Any,
        store: str,
        game_id: str,
        key: str,
    ) -> Result:
        """Inner sync_up body — runs with the per-game lock held.

        Pre-flight: bail out if there's no local dir to push from.
        Compare local against the remote manifest; if local hasn't
        moved relative to remote, skip the copy. Otherwise push
        local → remote, write the manifest on BOTH sides so a
        subsequent sync_down sees a no-op.
        """
        local_dir = self.get_local_save_dir(store, game_id)
        remote_dir = str(Path(self._cloud_root) / store, game_id)

        if not await asyncio.to_thread(lambda: Path(local_dir).is_dir()):
            await self._emit_up("CLOUD_SYNC_UP_COMPLETE", store, game_id, synced=False)
            return Result(success=True)

        remote_manifest = await read_manifest(remote_dir)
        local_modified = await self._is_modified(local_dir, remote_manifest)

        if not local_modified:
            await self._emit_up("CLOUD_SYNC_UP_COMPLETE", store, game_id, synced=False)
            return Result(success=True)

        await self._copy_tree(local_dir, remote_dir)
        new_manifest = await self._build_manifest(remote_dir)
        await write_manifest(remote_dir, new_manifest)
        # Mirror the manifest locally so the next sync_down sees
        # the sides matched and short-circuits to "no-op".
        await write_manifest(local_dir, new_manifest)

        await self._emit_up("CLOUD_SYNC_UP_COMPLETE", store, game_id, synced=True)
        return Result(success=True)

    async def sync_up(self: Any, store: str, game_id: str) -> Result:
        """Push the local save to the cloud after the game exits.

        No-op success when ``_cloud_root`` is unset. Acquires the
        per-game ``asyncio.Lock`` (atomic, bounded by
        ``_sync_wait_timeout`` — typically waits for any in-flight
        sync_down to finish), then delegates to
        :meth:`_sync_up_locked`. Emits
        ``CLOUD_SYNC_UP_{COMPLETE,FAILED}``.
        """
        if not self._cloud_root:
            return Result(success=True)

        key = f"{store}:{game_id}"
        lock, timeout_result = await self._acquire_sync_lock(
            key, "CLOUD_SYNC_UP_FAILED", store, game_id,
        )
        if lock is None:
            return timeout_result  # type: ignore[return-value]

        try:
            return await self._sync_up_locked(store, game_id, key)
        except Exception as e:
            logger.exception("[CloudSaveService] sync_up failed for %s", key)
            await self._emit_up("CLOUD_SYNC_UP_FAILED", store, game_id, error=str(e))
            return Result(success=False, error=str(e))
        finally:
            lock.release()

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
        lock = _get_lock(self._syncing, key)

        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._sync_wait_timeout)
        except asyncio.TimeoutError:
            return Result(success=False, error="sync_wait_timeout")

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
            logger.exception("[CloudSaveService] resolve_conflict failed for %s", key)
            return Result(success=False, error=str(e))
        finally:
            lock.release()

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
