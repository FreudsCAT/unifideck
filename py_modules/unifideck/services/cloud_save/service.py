"""services/cloud_save/service.py — Cloud save synchronization.

Subscribes to lifecycle events so saves sync around launches:
- ``GAME_LAUNCHED`` → ``sync_down``
- ``GAME_STOPPED`` → ``sync_up``

Shell class composing ``_SyncMixin``. Backend is filesystem-agnostic: treats ``<cloud_root>`` as a plain tree keyed by
``store/game_id`` so users can plug in Syncthing, rclone,
Dropbox, Nextcloud, etc.

Known limitation: native Linux games ``exec`` over bash,
bypassing the EXIT trap. For those, GAME_STOPPED never fires
and sync_up doesn't run. Fix requires replacing exec with
call+wait+exit — out of scope until validated on hardware.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...core.types import Events
from ...event_bus.event_bus import EventBus
from ...event_bus.event_bus_devex import subscribe
from .sync import _SyncMixin

if TYPE_CHECKING:
    from ...config import ConfigManager

logger = logging.getLogger(__name__)


class CloudSaveService(_SyncMixin):
    """Reactive cloud save sync for game launches."""

    def __init__(
        self,
        bus: EventBus,
        local_save_root: str,
        cloud_root: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Wire subscriptions + tunables from config.

        ``cloud_root=None`` disables the service — every sync
        method becomes a no-op success. Init ``_syncing`` map
        for per-game serialisation, read
        ``cloud.tolerance_seconds`` and
        ``cloud.sync_wait_timeout_seconds`` from config,
        ``auto_wire`` so ``@subscribe`` handlers register.
        """
        self._bus = bus
        self._local_root = local_save_root
        self._cloud_root = cloud_root

        self._syncing: dict[str, asyncio.Event] = {}

        self._tolerance = 2.0
        self._sync_wait_timeout = 30.0

        if config:
            self._tolerance = config.get("cloud.tolerance_seconds", self._tolerance)
            self._sync_wait_timeout = config.get("cloud.sync_wait_timeout_seconds", self._sync_wait_timeout)

        self._bus.auto_wire(self)

        if not self._cloud_root:
            logger.info("[CloudSaveService] starting disabled (no cloud_root configured)")
        else:
            logger.info("[CloudSaveService] starting with cloud_root=%s", self._cloud_root)

    async def stop(self) -> None:
        """Unsubscribe from EventBus events (shutdown/tests).

        Waits briefly for any in-flight syncs to complete so
        shutdown doesn't leave a half-copied save directory.
        """
        self._bus.unsubscribe_all(self)

        # Collect any active sync events that are not set
        active_events = [ev.wait() for ev in self._syncing.values() if not ev.is_set()]
        if active_events:
            logger.info("[CloudSaveService] waiting for %d in-flight syncs", len(active_events))
            # Wait up to a few seconds for pending syncs
            done, pending = await asyncio.wait(active_events, timeout=5.0)
            if pending:
                logger.warning("[CloudSaveService] shut down with %d syncs incomplete", len(pending))

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs: Any) -> None:
        """Download saves before the game starts.

        Extracts ``store`` + ``game_id`` from kwargs, delegates
        to ``sync_down``. Sync errors are logged — launch proceeds
        regardless so cloud problems never block gameplay.
        """
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")

        if not store or not game_id:
            return

        # Fire and forget; background task
        asyncio.create_task(self.sync_down(store, game_id))

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """Upload saves after the game exits.

        Delegates to ``sync_up``. Runs even on non-zero exit code
        — a game may have saved progress before crashing.
        """
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")

        if not store or not game_id:
            return

        # Fire and forget; background task
        asyncio.create_task(self.sync_up(store, game_id))

    def get_local_save_dir(self, store: str, game_id: str) -> str:
        """Public accessor for a game's local save directory.

        Used by diagnostic RPCs (``list_save_folder``) and by
        the cloud sync orchestrator to know where to pull from.
        Returns the absolute path under ``_local_root``.
        """
        import os
        return os.path.join(self._local_root, store, game_id)
