"""Cloud save service orchestration.

OP-17a | py_modules/unifideck/services/cloud_save/service.py

``CloudSaveService`` is the public API :

* ``sync_before_launch(game)`` — pull remote saves before launching;
* ``sync_after_exit(game)``    — push local saves after game exit;
* ``status(game)`` — last sync timestamp + direction;
* ``conflicts(game)`` — list unresolved conflicts (rare).

Strategy is "newer wins" (compare ``mtime``) with a manifest of
known save files per game stored under ``ServicePaths``. Conflicts
are surfaced to the user via a bus event rather than auto-resolved.
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
    """Cloud save service."""

    def __init__(
        self,
        bus: EventBus,
        local_save_root: str,
        cloud_root: str | None,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
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
        """Stop."""
        self._bus.off(Events.GAME_LAUNCHED, self._on_game_launched)
        self._bus.off(Events.GAME_STOPPED, self._on_game_stopped)

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs: Any) -> None:
        """On game launched."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id and self._cloud_root:
            await self.sync_down(store, game_id)

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """On game stopped."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id and self._cloud_root:
            await self.sync_up(store, game_id)

    def get_local_save_dir(self, store: str, game_id: str) -> str:
        """Get local save dir."""
        return local_save_dir(self._local_root, store, game_id)
