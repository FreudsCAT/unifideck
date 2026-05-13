"""Cloud-save service — pull saves on launch, push saves on exit.

OP-17a | py_modules/unifideck/services/cloud_save/service.py

``CloudSaveService`` mirrors local game-save directories to/from a
user-configured "cloud root" (typically a Steam Cloud mountpoint or
a Syncthing folder). The mirror is bidirectional, mtime-based, and
driven entirely by bus events:

* on ``GAME_LAUNCHED``: pull the cloud copy into the local save dir
  if the cloud copy is fresher (``sync_down``);
* on ``GAME_STOPPED``: push the local copy back to the cloud root
  if the local copy is fresher (``sync_up``).

When ``cloud_root`` is ``None`` the service still subscribes but no-
ops on every event — this lets the service be constructed
unconditionally without forcing the user to configure a cloud
backend.

The ``_syncing`` dict tracks in-flight sync operations per game so
a ``GAME_STOPPED`` event arriving mid-``GAME_LAUNCHED`` sync can
wait for the pull to complete before starting the push (avoiding
the obvious race condition).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...core.types import Events
from ...event_bus.event_bus import EventBus
from ...event_bus.event_bus_devex import auto_wire, subscribe
from ...utils.config_helpers import get_cfg
from .paths import local_save_dir
from .sync import _SyncMixin

if TYPE_CHECKING:
    from ...config import ConfigManager

logger = logging.getLogger(__name__)


class CloudSaveService(_SyncMixin):
    """Mirror per-game saves between the local Steam Deck and a cloud root."""

    def __init__(
        self,
        bus: EventBus,
        local_save_root: str,
        cloud_root: str | None,
        config: ConfigManager | None = None,
    ) -> None:
        """Wire the service to the bus and load tunables.

        Reads three tunables from the config:

        * ``cloud.enabled`` (default ``True``) — master kill switch;
        * ``cloud.tolerance_seconds`` (default 2) — mtime tolerance
          used by ``_SyncMixin``; differences below this are
          considered "same file" to avoid spurious syncs caused by
          filesystem mtime quirks (e.g. NTFS rounding);
        * ``cloud.sync_wait_timeout_seconds`` (default 5) — maximum
          wait when a sync request arrives during an in-flight sync
          on the same game.

        Args:
            bus: live event bus.
            local_save_root: directory under which per-game local
                save directories live (typically
                ``<data_dir>/saves``).
            cloud_root: directory under which cloud-side save
                mirrors live, or ``None`` if cloud sync is
                disabled.
            config: optional config manager.
        """
        self._bus = bus
        self._local_root = local_save_root
        self._cloud_root = cloud_root
        self._syncing: dict[str, asyncio.Event] = {}
        self._enabled = bool(get_cfg(config, "cloud.enabled", True))
        self._tolerance = float(
            get_cfg(config, "cloud.tolerance_seconds", 2),
        )
        self._sync_wait_timeout = float(
            get_cfg(config, "cloud.sync_wait_timeout_seconds", 5),
        )
        auto_wire(self, self._bus)
        logger.info(
            "[CloudSaveService] wired (2 subscriptions, cloud=%s)",
            self._cloud_root or "DISABLED",
        )

    async def stop(self) -> None:
        """Unsubscribe both bus handlers on plugin shutdown.

        In-flight syncs (if any) are *not* cancelled — they're
        awaited implicitly by the unsubscribe call. This ensures a
        push triggered just before unload completes before the
        plugin process exits.
        """
        self._bus.off(Events.GAME_LAUNCHED, self._on_game_launched)
        self._bus.off(Events.GAME_STOPPED, self._on_game_stopped)

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs: Any) -> None:
        """Pull cloud → local before the game starts using saves.

        No-ops if either the payload is incomplete or no
        ``cloud_root`` is configured. The actual mtime comparison
        and copy logic live in ``_SyncMixin.sync_down``.
        """
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id and self._cloud_root:
            await self.sync_down(store, game_id)

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """Push local → cloud after the game finishes writing saves.

        Symmetric to ``_on_game_launched`` but inverts the sync
        direction. ``sync_up`` waits for any in-flight ``sync_down``
        on the same game (via ``self._syncing``) before starting,
        so the push always sees the post-launch state of the local
        save directory, never a partial mid-pull state.
        """
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id and self._cloud_root:
            await self.sync_up(store, game_id)

    def get_local_save_dir(self, store: str, game_id: str) -> str:
        """Return the canonical local-save directory for a game.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            Absolute filesystem path to the per-game save
            directory under ``local_save_root``. Does not create
            the directory — that's the responsibility of the sync
            logic when actually writing files.
        """
        return local_save_dir(self._local_root, store, game_id)
