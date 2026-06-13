"""services/cloud_save/service.py — Cloud save synchronization.

Subscribes to lifecycle events so saves sync around launches:
- ``GAME_LAUNCHED`` → ``sync_down``
- ``GAME_STOPPED`` → ``sync_up``
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events, Result
from unifideck.event_bus.event_bus import EventBus
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe
from unifideck.launcher.proton.infrastructure.prefix_layout import resolve_drive_c

from .epic_strategy import EpicCloudSaveStrategy
from .gog_strategy import GOGCloudSaveStrategy
from .safety import SaveConflictError
from .sync import _SyncMixin

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

# Strong references to background sync tasks so the GC can't
# collect them mid-flight (see RUF006).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _track(task: asyncio.Task[Any]) -> None:
    """Register a fire-and-forget task so the GC doesn't collect it early."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


class CloudSaveService(_SyncMixin):
    """Reactive cloud save sync for game launches."""

    def __init__(
        self,
        bus: EventBus,
        local_save_root: str,
        cloud_root: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        self._bus = bus
        self._local_root = local_save_root
        self._cloud_root = cloud_root
        self._config = config

        self._syncing: dict[str, asyncio.Lock] = {}
        self._tolerance = 2.0
        self._sync_wait_timeout = 30.0

        if config:
            self._tolerance = config.get("cloud.tolerance_seconds", self._tolerance)
            self._sync_wait_timeout = config.get("cloud.sync_wait_timeout_seconds", self._sync_wait_timeout)

        # Initialize store strategies
        self._strategies = {
            "epic": EpicCloudSaveStrategy(self._local_root, config),
            "gog": GOGCloudSaveStrategy(self._local_root, config),
        }

        auto_wire(self, self._bus)

        if not self._cloud_root:
            logger.info("[CloudSaveService] starting without cloud_root backup fallback")
        else:
            logger.info("[CloudSaveService] starting with cloud_root=%s", self._cloud_root)

    async def stop(self) -> None:
        """Unsubscribe from EventBus events (shutdown/tests)."""
        self._bus.unsubscribe_all(self)

        in_flight = [
            (key, lock) for key, lock in self._syncing.items()
            if lock.locked()
        ]
        if not in_flight:
            return
        logger.info(
            "[CloudSaveService] waiting for %d in-flight syncs",
            len(in_flight),
        )

        async def _drain(lock: asyncio.Lock) -> None:
            await lock.acquire()
            lock.release()

        try:
            await asyncio.wait_for(
                asyncio.gather(*(_drain(lock) for _key, lock in in_flight)),
                timeout=5.0,
            )
        except TimeoutError:
            still_held = [
                key for key, lock in in_flight if lock.locked()
            ]
            logger.warning(
                "[CloudSaveService] shut down with %d syncs incomplete: %s",
                len(still_held), still_held,
            )

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs: Any) -> None:
        """Download saves before the game starts."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")

        if not store or not game_id:
            return

        # Fire and forget; background task
        _track(asyncio.create_task(self.sync_down(store, game_id)))

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """Upload saves after the game exits."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")

        if not store or not game_id:
            return

        # Fire and forget; background task
        _track(asyncio.create_task(self.sync_up(store, game_id)))

    def _detect_wine_prefix_save_dir(self, game_id: str) -> str | None:
        """Attempt to auto-detect common locations under the wine prefix."""
        try:
            prefix_root = Path(self._local_root).parent / "prefixes" / game_id
            drive_c = resolve_drive_c(prefix_root)
            if not drive_c:
                return None

            game_title = ""
            if self._config:
                game_title = self._config.get(f"games.{game_id}.title") or ""

            candidates = [
                drive_c / "users" / "steamuser" / "Saved Games",
                drive_c / "users" / "steamuser" / "Documents",
                drive_c / "users" / "steamuser" / "AppData" / "Local",
                drive_c / "users" / "steamuser" / "AppData" / "Roaming",
            ]
            for candidate in candidates:
                if candidate.is_dir():
                    if game_title:
                        # Clean and normalize title for directory name matching
                        safe_title = re.sub(r"[^a-zA-Z0-9]", "", game_title).lower()
                        for child in candidate.iterdir():
                            if child.is_dir():
                                child_name = re.sub(r"[^a-zA-Z0-9]", "", child.name).lower()
                                if safe_title in child_name or child_name in safe_title:
                                    logger.info("[CloudSave] Auto-detected Wine prefix save dir: %s", child)
                                    return str(child)
        except Exception as e:
            logger.debug("[CloudSave] Failed to auto-detect save dir: %s", e)
        return None

    def get_local_save_dir(self, store: str, game_id: str) -> str:
        """Public accessor for a game's local save directory."""
        if self._config:
            configured = self._config.get(f"games.{game_id}.save_path")
            if configured:
                return str(configured)

        if store in self._strategies:
            strat_dir = self._strategies[store].get_local_save_dir(game_id)
            if strat_dir:
                return strat_dir

        # Try to auto-detect prefix folder
        detected = self._detect_wine_prefix_save_dir(game_id)
        if detected:
            return detected

        return str(Path(self._local_root) / store / game_id)

    async def sync_down(self, store: str, game_id: str, force: bool = False) -> Result:
        """Pull cloud saves before a game launch.

        ``force`` pulls the cloud copy unconditionally (explicit "Use Cloud");
        the automatic on-launch path leaves it False so newer local saves are
        never silently overwritten.
        """
        if self._config and not self._config.get_bool("cloud.enabled", True):
            logger.info("[CloudSaveService] Cloud sync is disabled globally")
            return Result(success=True)

        key = f"{store}:{game_id}"
        lock, timeout_result = await self._acquire_sync_lock(
            key, "CLOUD_SYNC_DOWN_FAILED", store, game_id,
        )
        if lock is None:
            return timeout_result if timeout_result is not None else Result(
                success=False, error="lock_acquire_failed",
            )

        try:
            success = True

            # 1. Run store-specific strategy sync down
            if store in self._strategies:
                logger.info("[CloudSaveService] Executing %s sync_down strategy for %s (force=%s)", store, game_id, force)
                success = await self._strategies[store].sync_down(game_id, force)

            # 2. Run fallback filesystem backup if configured
            if self._cloud_root:
                logger.info("[CloudSaveService] Executing fallback sync_down for %s", game_id)
                fallback_res = await self._sync_down_locked(store, game_id, key)
                success = success and fallback_res.success

            return Result(success=success)
        except Exception as e:
            logger.exception("[CloudSaveService] sync_down failed for %s", key)
            await self._emit_down("CLOUD_SYNC_DOWN_FAILED", store, game_id, error=str(e))
            return Result(success=False, error=str(e))
        finally:
            lock.release()

    async def sync_up(self, store: str, game_id: str) -> Result:
        """Push saves after a game exits."""
        if self._config and not self._config.get_bool("cloud.enabled", True):
            logger.info("[CloudSaveService] Cloud sync is disabled globally")
            return Result(success=True)

        key = f"{store}:{game_id}"
        lock, timeout_result = await self._acquire_sync_lock(
            key, "CLOUD_SYNC_UP_FAILED", store, game_id,
        )
        if lock is None:
            return timeout_result if timeout_result is not None else Result(
                success=False, error="lock_acquire_failed",
            )

        try:
            success = True

            # 1. Run fallback filesystem backup first if configured
            if self._cloud_root:
                logger.info("[CloudSaveService] Executing fallback sync_up for %s", game_id)
                fallback_res = await self._sync_up_locked(store, game_id, key)
                success = success and fallback_res.success

            # 2. Run store-specific strategy sync up
            if store in self._strategies:
                logger.info("[CloudSaveService] Executing %s sync_up strategy for %s", store, game_id)
                try:
                    strategy_success = await self._strategies[store].sync_up(game_id)
                    success = success and strategy_success
                except SaveConflictError as conflict:
                    # The strategy refused to push because the local copy
                    # would WIPE the cloud saves. Never auto-destroy — and
                    # never treat this as a launch failure (saves are intact
                    # and locally backed up).
                    if conflict.hard:
                        # HARD: empty / no-save-data. Uploading nothing could
                        # only wipe the cloud, so it's never a valid choice —
                        # surface a plain error, not a "keep local" pick.
                        logger.error(  # noqa: TRY400 — expected guard, no traceback wanted
                            "[CloudSaveService] sync_up REFUSED for %s (%s) — "
                            "no local save data; cloud copy preserved",
                            key, conflict.reason,
                        )
                        await self._emit_save_error(store, game_id)
                    else:
                        # SOFT: local still has saves but diverged/regressed —
                        # surface the conflict modal so the user picks.
                        logger.warning(
                            "[CloudSaveService] sync_up BLOCKED for %s (%s) — "
                            "raising cloud-save conflict instead of wiping",
                            key, conflict.reason,
                        )
                        await self._emit_save_conflict(store, game_id, conflict)

            return Result(success=success)
        except Exception as e:
            logger.exception("[CloudSaveService] sync_up failed for %s", key)
            await self._emit_up("CLOUD_SYNC_UP_FAILED", store, game_id, error=str(e))
            return Result(success=False, error=str(e))
        finally:
            lock.release()

    async def _emit_save_conflict(
        self, store: str, game_id: str, conflict: SaveConflictError,
    ) -> None:
        """Surface a blocked upload as a user-facing cloud-save conflict.

        Reuses the existing ``retry-sync`` modal flow: emits a
        ``LAUNCHER_STAGE`` event carrying the local snapshot and a
        ``retry-sync`` action so ``CloudSaveConflictModal`` opens. The
        modal's "Use Cloud" choice dispatches ``retry-sync … sync_down``
        which re-pulls the cloud saves (the safe resolution for a local
        regression); "Use Local" maps to ``sync_up`` which simply hits this
        same guard again — we never auto-overwrite the cloud.
        """
        if not self._bus:
            return
        game_title = ""
        if self._config:
            game_title = self._config.get(f"games.{game_id}.title") or ""
        await self._bus.emit(
            Events.LAUNCHER_STAGE,
            store=store,
            game_id=game_id,
            # Non-empty i18n_key so the listener doesn't early-return before
            # opening the modal (it renders its own copy from cloudSave.*).
            i18n_key="cloudSave.title",
            severity="warning",
            game_title=game_title or game_id,
            local_snapshot=conflict.local,
            remote_snapshot=self._cloud_snapshot(store, game_id),
            action={
                "verb": "retry-sync",
                "args": [store, game_id, "sync_down"],
            },
        )

    def _cloud_snapshot(self, store: str, game_id: str) -> dict:
        """Best-effort ``{timestamp, file_count, total_bytes}`` for the
        cloud-side copy, to populate the conflict modal.

        Uses the plugin's local cloud backup (``_cloud_root``) when present,
        else the most recent versioned save backup — both are local and
        cheap. A live store-cloud listing would mean a full download just to
        render a modal, so we approximate from the nearest local mirror.
        """
        from .safety import latest_backup_snapshot, snapshot
        if self._cloud_root:
            remote_dir = Path(self._cloud_root) / store / game_id
            if remote_dir.is_dir():
                return snapshot(remote_dir)
        return latest_backup_snapshot(store, game_id)

    async def _emit_save_error(
        self, store: str, game_id: str,
    ) -> None:
        """Surface a HARD-blocked (empty) upload as a plain error toast.

        No ``retry-sync`` action → the listener shows an error toast, not
        the pick modal: uploading nothing is never a valid choice, so we
        don't offer one. The cloud copy is left untouched.
        """
        if not self._bus:
            return
        game_title = ""
        if self._config:
            game_title = self._config.get(f"games.{game_id}.title") or ""
        await self._bus.emit(
            Events.LAUNCHER_STAGE,
            store=store,
            game_id=game_id,
            i18n_key="cloudSave.uploadBlockedEmpty",
            i18n_params={"game": game_title or game_id},
            severity="error",
        )
