"""Steam shortcut orchestration — the public ``ShortcutService`` class.

OP-14a | py_modules/unifideck/services/shortcut/service.py

``ShortcutService`` is the multi-inheritance facade that composes :

* ``_VdfShortcutsMixin`` — read/write the ``shortcuts.vdf`` binary file;
* ``_GamesMapMixin``     — maintain the Unifideck-side game → appid map;
* ``EventsMixin``        — emit bus events on shortcut create/update/delete;
* ``auth_shortcut``      — the special-case auth shortcut for Ubisoft/Epic;
* ``persistence``        — read/write the on-disk state on boot/save.

The class is intentionally a thin shell over the mixins so individual
responsibilities can be tested in isolation.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from ...core.types import Events
from ...event_bus.event_bus_devex import auto_wire
from . import persistence
from .events import EventsMixin
from .games_map import GameMapEntry, generate_app_id
from .games_map_mixin import UNIFIDECK_TAG, _GamesMapMixin
from .vdf_shortcuts import _VdfShortcutsMixin

if TYPE_CHECKING:
    from ...event_bus.event_bus import EventBus
logger = logging.getLogger(__name__)
__all__ = ["ShortcutService", "UNIFIDECK_TAG"]


class ShortcutService(
    _GamesMapMixin,
    _VdfShortcutsMixin,
    EventsMixin,
):
    """Shortcut service."""

    def __init__(
        self,
        bus: EventBus,
        shortcuts_path: str,
        games_map_path: str,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._shortcuts_path = shortcuts_path
        self._games_map_path = games_map_path
        self._shortcuts: list[dict[str, Any]] = []
        self._games_map: dict[str, GameMapEntry] = {}
        self._shortcuts_loaded: bool = False
        self._games_map_loaded: bool = False
        auto_wire(self, self._bus)
        logger.info("[ShortcutService] wired (3 subscriptions)")

    async def stop(self) -> None:
        """Stop."""
        self._bus.off(
            Events.DOWNLOAD_COMPLETE,
            self._on_download_complete,
        )
        self._bus.off(
            Events.GAME_UNINSTALLED,
            self._on_game_uninstalled,
        )
        self._bus.off(
            Events.SYNC_COMPLETE,
            self._on_sync_complete,
        )

    @staticmethod
    def generate_app_id(exe: str, title: str) -> int:
        """Generate app ID."""
        return generate_app_id(exe, title)

    async def _load_shortcuts(self) -> None:
        """Load shortcuts."""
        if self._shortcuts_loaded:
            return
        self._shortcuts_loaded = True
        self._shortcuts = await persistence.load_shortcuts(
            self._shortcuts_path,
        )

    async def _load_games_map(self) -> None:
        """Load games map."""
        if self._games_map_loaded:
            return
        self._games_map_loaded = True
        self._games_map = await persistence.load_games_map(
            self._games_map_path,
        )

    async def _save_all(self) -> None:
        """Save all."""
        await persistence.save_all(
            self._shortcuts_path,
            self._shortcuts,
            self._games_map_path,
            self._games_map,
        )
