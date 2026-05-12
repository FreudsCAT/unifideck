"""Steam shortcut service — public ``ShortcutService`` facade.

OP-14a | py_modules/unifideck/services/shortcut/service.py

``ShortcutService`` glues together the three mixins that handle the
different aspects of Steam shortcut management:

* ``_VdfShortcutsMixin`` — read/write the binary ``shortcuts.vdf``;
* ``_GamesMapMixin``     — maintain the Unifideck-side
  ``(store, game_id) → AppID`` map;
* ``EventsMixin``        — react to bus events
  (``DOWNLOAD_COMPLETE``, ``GAME_UNINSTALLED``, ``SYNC_COMPLETE``).

The class itself is just a constructor + a couple of orchestration
methods (load / save). Everything else lives in the mixins so each
concern is independently testable.

State is loaded lazily on first access (``_load_shortcuts`` /
``_load_games_map``) rather than at construction time — the
shortcuts.vdf file can be large and parsing it on every boot would
slow plugin startup; deferring to first use means the file is only
read when actually needed.
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
    """Facade composing the three shortcut concerns into one service."""

    def __init__(
        self,
        bus: EventBus,
        shortcuts_path: str,
        games_map_path: str,
    ) -> None:
        """Wire the service and auto-subscribe its event handlers.

        State is lazy: ``_shortcuts`` and ``_games_map`` start
        empty, marked unloaded via the ``_loaded`` flags. The
        first call to a mixin method that needs them will trigger
        ``_load_shortcuts`` / ``_load_games_map``.

        Args:
            bus: live event bus.
            shortcuts_path: absolute path to Steam's
                ``shortcuts.vdf`` file.
            games_map_path: absolute path to Unifideck's
                games-map JSON file.
        """
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
        """Unsubscribe every bus handler on plugin shutdown.

        Explicit ``self._bus.off`` calls rather than relying on
        garbage collection — the bus holds strong references to
        subscribers, and we want the unsubscribe to be
        synchronous and predictable.
        """
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
        """Compute the canonical AppID for a non-Steam shortcut.

        Re-exports ``games_map.generate_app_id`` as a class method
        so callers (typically the RPC layer) don't need to import
        the implementation module directly.

        Args:
            exe: executable path used as the AppID seed.
            title: display title used as the AppID seed.

        Returns:
            Steam-style 32-bit unsigned AppID.
        """
        return generate_app_id(exe, title)

    async def _load_shortcuts(self) -> None:
        """Lazily parse ``shortcuts.vdf`` into the in-memory list.

        Idempotent: the ``_shortcuts_loaded`` flag prevents
        re-parsing. The flag is set **before** the actual load so
        concurrent calls during plugin boot don't double-load
        (each call would start the parse before the first one
        finished).
        """
        if self._shortcuts_loaded:
            return
        self._shortcuts_loaded = True
        self._shortcuts = await persistence.load_shortcuts(
            self._shortcuts_path,
        )

    async def _load_games_map(self) -> None:
        """Lazily load the games map JSON into memory.

        Same idempotency pattern as ``_load_shortcuts``.
        """
        if self._games_map_loaded:
            return
        self._games_map_loaded = True
        self._games_map = await persistence.load_games_map(
            self._games_map_path,
        )

    async def _save_all(self) -> None:
        """Persist both ``shortcuts.vdf`` and the games map atomically.

        Both files are written via temp + rename inside the
        persistence layer so neither can be left half-written by
        a crash mid-save. Called after every state mutation
        (shortcut create / update / remove).
        """
        await persistence.save_all(
            self._shortcuts_path,
            self._shortcuts,
            self._games_map_path,
            self._games_map,
        )
