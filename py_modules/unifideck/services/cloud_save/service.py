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
        cache: Any = None,
    ) -> None:
        self._bus = bus
        self._local_root = local_save_root
        self._cloud_root = cloud_root
        self._config = config
        self._cache = cache

        self._syncing: dict[str, asyncio.Lock] = {}
        self._tolerance = 2.0
        self._sync_wait_timeout = 30.0

        if config:
            self._tolerance = config.get("cloud.tolerance_seconds", self._tolerance)
            self._sync_wait_timeout = config.get("cloud.sync_wait_timeout_seconds", self._sync_wait_timeout)

        # Initialize store strategies
        self._strategies = {
            "epic": EpicCloudSaveStrategy(self._local_root, config, cache),
            "gog": GOGCloudSaveStrategy(self._local_root, config, cache),
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
        """Download saves before the game starts (when auto-pull is enabled).

        ``sync_down`` is the non-destructive direction (it snapshots before
        pulling and never deletes local saves), so it stays automatic by
        default. Gated by ``cloud.auto_pull_on_launch`` so users can opt out.
        """
        if not self._auto_enabled("auto_pull_on_launch", default=True):
            return
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")

        if not store or not game_id:
            return

        # Fire and forget; background task
        _track(asyncio.create_task(self.sync_down(store, game_id)))

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """Upload saves after the game exits — ONLY when auto-push is enabled.

        Uploads are the destructive direction (they can overwrite cloud saves),
        so by default this is OFF: the user pushes deliberately via the
        cloud-save button. Set ``cloud.auto_push_on_stop`` true to restore
        fully-automatic sync.
        """
        if not self._auto_enabled("auto_push_on_stop", default=False):
            return
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")

        if not store or not game_id:
            return

        # Fire and forget; background task
        _track(asyncio.create_task(self.sync_up(store, game_id)))

    def _auto_enabled(self, key: str, *, default: bool) -> bool:
        """Read a ``cloud.<key>`` boolean flag, falling back to ``default``."""
        if not self._config:
            return default
        try:
            return bool(self._config.get(f"cloud.{key}", default))
        except Exception:
            return default

    def auto_sync_enabled(self, direction: str) -> bool:
        """Whether AUTOMATIC sync is enabled for ``direction`` ("down"/"up").

        Public so the launcher's auto-sync phase respects the same flags as the
        event subscribers — ``down`` ⇒ ``auto_pull_on_launch`` (default on),
        ``up`` ⇒ ``auto_push_on_stop`` (default off). Manual button/conflict
        syncs call ``sync_down``/``sync_up`` directly and are never gated here.
        """
        if direction == "down":
            return self._auto_enabled("auto_pull_on_launch", default=True)
        if direction == "up":
            return self._auto_enabled("auto_push_on_stop", default=False)
        return True

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

    def get_local_save_dir(self, store: str, game_id: str) -> str | None:
        """Resolve a game's ACTUAL local save directory, or ``None``.

        Order: explicit config override → store strategy (prefix-resolved) →
        wine-prefix auto-detect. Returns ``None`` when no real location can be
        found (e.g. the game was never launched, so no prefix exists). We do
        NOT fall back to a ``saves/<store>/<id>`` staging dir — the game never
        reads from there, so syncing/backing it up only strands saves.
        """
        if self._config:
            configured = self._config.get(f"games.{game_id}.save_path")
            if configured:
                return str(configured)

        if store in self._strategies:
            strat_dir = self._strategies[store].get_local_save_dir(game_id)
            if strat_dir:
                return strat_dir

        # Try to auto-detect prefix folder (real, in-prefix location).
        return self._detect_wine_prefix_save_dir(game_id)

    # ── Manual cloud-save button: status surface ─────────────────────
    def is_syncing(self, store: str, game_id: str) -> bool:
        """True if a sync for this game is currently in flight."""
        lock = self._syncing.get(f"{store}:{game_id}")
        return bool(lock and lock.locked())

    def _cloud_supported(self, store: str, game_id: str) -> bool | None:
        """Native cloud-save support for ``store``, from enriched metadata.

        Reads the ``cloud`` flag baked in by the unifiDB save-location
        pipeline (Ludusavi). ``None`` when unknown (no enriched data) — many
        Epic games genuinely lack cloud support, which is why "didn't sync"
        is sometimes "nothing to sync".
        """
        if self._cache is None:
            return None
        try:
            meta = self._cache.get("metadata", f"{store}:{game_id}")
        except Exception:
            return None
        if isinstance(meta, dict):
            cloud = meta.get("cloud")
            if isinstance(cloud, dict) and store in cloud:
                return bool(cloud[store])
        return None

    def _browse_start(self, game_id: str, save_dir: str | None) -> str:
        """Best starting folder for the manual save-location picker.

        Prefers the game's prefix user dir (where in-prefix saves live), then an
        existing resolved/fallback save dir, then the user's home.
        """
        users = (
            Path(self._local_root).parent / "prefixes" / game_id
            / "drive_c" / "users" / "steamuser"
        )
        if users.is_dir():
            return str(users)
        if save_dir and Path(save_dir).is_dir():
            return str(save_dir)
        return str(Path.home())

    async def get_cloud_status(self, store: str, game_id: str) -> dict:
        """Out-of-band status for the manual cloud-save button.

        Possibly slow (``get_local_save_dir`` may hit the store's metadata on
        first call), so callers must fetch this off the render hot path.
        ``has_cloud_saves`` is best-effort: ``True`` when a local cloud
        mirror/backup has files, ``False`` when the store has no cloud support,
        else ``None`` (unknown without a full download).
        """
        from .safety import has_save_data, snapshot
        supported = store in self._strategies
        status: dict[str, Any] = {
            "supported": supported,
            "in_progress": self.is_syncing(store, game_id),
            "auto_pull": self._auto_enabled("auto_pull_on_launch", default=True),
            "auto_push": self._auto_enabled("auto_push_on_stop", default=False),
            "cloud_supported": self._cloud_supported(store, game_id),
            "save_path": None,
            "save_path_resolved": False,
            "has_local_saves": False,
            "local_snapshot": {},
            "has_cloud_saves": None,
            "remote_snapshot": None,
            "last_sync_ts": 0.0,
            "browse_start": str(Path.home()),
        }
        if not supported:
            return status
        save_dir = ""
        try:
            save_dir = self.get_local_save_dir(store, game_id)
        except Exception as e:
            logger.debug("[CloudSave] status: get_local_save_dir failed: %s", e)
        status["browse_start"] = self._browse_start(game_id, save_dir)
        # save_dir is always a REAL resolved location now (or None) — the
        # staging fallback is gone, so there's no stale dir to misreport as
        # "local saves" (e.g. after the prefix was deleted).
        if save_dir:
            status["save_path"] = save_dir
            status["save_path_resolved"] = True
            if Path(save_dir).is_dir():
                status["has_local_saves"] = has_save_data(save_dir)
                status["local_snapshot"] = snapshot(save_dir)
        remote = self._cloud_snapshot(store, game_id)
        # Prefer the REAL store-cloud timestamp/presence (e.g. legendary
        # list-saves) over the local backup mirror, which can be stale and
        # would make "Cloud" misleadingly differ from "Local". File count/size
        # still come from the mirror as a rough hint.
        cloud_info = await self._real_cloud_info(store, game_id)
        if cloud_info is not None:
            has = bool(cloud_info.get("has_saves"))
            status["has_cloud_saves"] = has
            if has:
                ts = float(cloud_info.get("timestamp") or 0.0)
                # ONLY what the real cloud reported — never backfill from the
                # local mirror (a stale/wrong size is worse than no size). 0
                # means "unknown" and the UI omits that field.
                status["remote_snapshot"] = {
                    "timestamp": ts,
                    "file_count": int(cloud_info.get("file_count") or 0),
                    "total_bytes": int(cloud_info.get("total_bytes") or 0),
                }
                status["last_sync_ts"] = ts
        elif remote and remote.get("file_count"):
            status["remote_snapshot"] = remote
            status["has_cloud_saves"] = True
            status["last_sync_ts"] = float(remote.get("timestamp") or 0.0)
        elif status["cloud_supported"] is False:
            status["has_cloud_saves"] = False
        return status

    async def _real_cloud_info(self, store: str, game_id: str) -> dict | None:
        """Real store-cloud save info via the strategy, timeout-bounded."""
        strat = self._strategies.get(store)
        if strat is None:
            return None
        try:
            return await asyncio.wait_for(
                strat.get_cloud_save_info(game_id), timeout=20,
            )
        except Exception:
            return None

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
            store_handled = store in self._strategies

            # The store's own tool (gogdl/legendary) is the ONLY cloud restore
            # source. We never pull from the local ~/Save Games Backup mirror.
            if store_handled:
                logger.info("[CloudSaveService] Executing %s sync_down strategy for %s (force=%s)", store, game_id, force)
                success = await self._strategies[store].sync_down(game_id, force)

            # Write-only safety backup of the now-current local saves.
            await self._backup_to_mirror(store, game_id)

            # Signal completion so the (fire-and-forget) frontend can react —
            # the manual Download runs as a background task and never blocks
            # the RPC, so the result is delivered via this event, not the call.
            if success:
                await self._emit_down("CLOUD_SYNC_DOWN_COMPLETE", store, game_id, synced=True)
            else:
                await self._emit_down("CLOUD_SYNC_DOWN_FAILED", store, game_id, error="sync_failed")
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
            store_handled = store in self._strategies
            conflict_handled = False

            # The store's own tool (gogdl/legendary) is the ONLY cloud upload
            # path. The ~/Save Games Backup mirror is write-only (see
            # _backup_to_mirror) — never a sync source.
            if store_handled:
                logger.info("[CloudSaveService] Executing %s sync_up strategy for %s", store, game_id)
                try:
                    success = await self._strategies[store].sync_up(game_id)
                except SaveConflictError as conflict:
                    conflict_handled = True
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

            # Write-only safety backup of the current local saves.
            await self._backup_to_mirror(store, game_id)

            # Signal completion for the fire-and-forget frontend (see sync_down).
            # On a conflict the upload was BLOCKED, not completed — the conflict
            # modal / error toast is the user-facing signal, so don't also emit
            # a COMPLETE (which would wrongly read as "uploaded").
            if not conflict_handled:
                if success:
                    await self._emit_up("CLOUD_SYNC_UP_COMPLETE", store, game_id, synced=True)
                else:
                    await self._emit_up("CLOUD_SYNC_UP_FAILED", store, game_id, error="sync_failed")
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

    async def _backup_to_mirror(self, store: str, game_id: str) -> None:
        """Write-only safety backup: mirror current local saves to ``_cloud_root``.

        The ``~/Save Games Backup`` mirror is WRITE-ONLY — we copy the current
        local saves into it after a sync, but NEVER pull from it. The only
        restore source is the store's own cloud (gogdl/legendary). Guarded so an
        empty/missing local dir never overwrites a good backup, and best-effort
        so a backup failure never affects the sync result.
        """
        if not self._cloud_root:
            return
        try:
            from .safety import has_save_data
            local_dir = self.get_local_save_dir(store, game_id)
            if not local_dir or not Path(local_dir).is_dir() or not has_save_data(local_dir):
                return
            mirror = Path(self._cloud_root) / store / game_id
            await self._copy_tree(local_dir, str(mirror))
            logger.info("[CloudSaveService] Backed up saves to mirror: %s", mirror)
        except Exception as e:
            logger.warning(
                "[CloudSaveService] backup-to-mirror failed (non-fatal) for %s:%s: %s",
                store, game_id, e,
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

    def _game_title(self, store: str, game_id: str) -> str:
        """Human-readable title for toasts: config → metadata cache → id.

        The raw store id (e.g. a GOG numeric id) is a poor thing to show a
        user, so prefer a real title wherever one is cached.
        """
        if self._config:
            title = self._config.get(f"games.{game_id}.title")
            if title:
                return str(title)
        if self._cache is not None:
            try:
                meta = self._cache.get("metadata", f"{store}:{game_id}")
            except Exception:
                meta = None
            if isinstance(meta, dict) and meta.get("title"):
                return str(meta["title"])
        return game_id

    async def _emit_save_error(
        self, store: str, game_id: str,
    ) -> None:
        """Surface a HARD-blocked (empty) upload as a short title+body toast.

        No ``retry-sync`` action → the listener shows a plain toast, not the
        pick modal: uploading nothing is never a valid choice. The cloud copy
        is left untouched. Severity is a warning (this is an expected skip
        when there are no local saves, not a failure).
        """
        if not self._bus:
            return
        await self._bus.emit(
            Events.LAUNCHER_STAGE,
            store=store,
            game_id=game_id,
            i18n_title_key="cloudSave.uploadSkippedTitle",
            i18n_key="cloudSave.uploadSkippedBody",
            i18n_params={"game": self._game_title(store, game_id)},
            severity="warning",
        )
