"""services/shortcut/service.py — ShortcutService facade class.

Non-Steam shortcut management. Mutates ``shortcuts.vdf``
(Steam's registry) and ``games.map`` (Unifideck's own exe
manifest read by the launcher wrapper at game-launch time).

Shell class composing multiple mixins:
- ``EventsMixin``       : ``@subscribe`` handlers
- ``_GamesMapMixin``    : typed mutations + queries
- ``_VdfShortcutsMixin``: escape-hatch read/write + auth
                          shortcut delegator

Shell itself owns ``__init__`` / ``stop`` / ``generate_app_id``
and three loaders that pair ``_loaded`` flags with
``persistence.py`` stateless helpers.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.event_bus.event_bus_devex import auto_wire

from .events import EventsMixin
from .games_map import GameMapEntry, generate_app_id
from .games_map_mixin import UNIFIDECK_TAG, _GamesMapMixin
from .persistence import (
    merge_foreign_shortcuts,
    read_games_map,
    read_vdf,
    write_games_map,
    write_vdf,
)
from .vdf_shortcuts import _VdfShortcutsMixin

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

__all__ = ["UNIFIDECK_TAG", "ShortcutService"]


class ShortcutService(
    EventsMixin,
    _GamesMapMixin,
    _VdfShortcutsMixin,
):
    """Facade for shortcuts.vdf + games.map mutations."""

    def __init__(
        self,
        bus: EventBus,
        shortcuts_path: str,
        games_map_path: str,
        launcher_path: str = "",
    ) -> None:
        """Store refs + paths, init empty state + per-file loaded flags."""
        self._bus = bus
        self._shortcuts_path = shortcuts_path
        self._games_map_path = games_map_path
        # ``launcher_path`` is the ``Exe`` written into every
        # generated ``shortcuts.vdf`` entry — Steam launches this
        # binary when the user clicks the tile, and it then reads
        # ``LaunchOptions`` (``"<store>:<game_id>"``) to install /
        # play the game via the right backend. Always set in
        # production via the service_defs wiring; defaults to ""
        # only for unit tests that exercise the mixin in isolation.
        self._launcher_path = launcher_path

        self._shortcuts: dict[str, Any] = {}
        # Type fix (lot 11g): the source of truth is
        # ``persistence.read_games_map`` which returns
        # ``dict[str, GameMapEntry]`` (the NamedTuple). The prior
        # declaration as ``dict[str, dict[str, str]]`` was a
        # drift artifact and caused mypy assignment errors at
        # every load site.
        self._games_map: dict[str, GameMapEntry] = {}

        self._shortcuts_loaded = False
        self._games_map_loaded = False

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` as if it were a bus
        # method, but ``auto_wire`` is module-level — the
        # call raised ``AttributeError`` and every
        # subscription was lost (caught and silenced upstream).
        auto_wire(self, self._bus)

    def set_shortcuts_path(self, shortcuts_path: str) -> None:
        """Re-point at a different user's ``shortcuts.vdf`` at runtime.

        Called when the active Steam user is (re)confirmed after boot — the
        frontend push or an account switch — via
        :func:`unifideck.steam.current_user.rebind_user_paths`. Resetting
        ``_shortcuts_loaded`` is load-bearing: the in-memory ``_shortcuts``
        dict is per-user, so without it the NEXT ``_save_all`` would write the
        *previous* user's cached entries into the new user's file. Clearing the
        cache forces a fresh read of the correct file on the next access.
        """
        if shortcuts_path == self._shortcuts_path:
            return
        self._shortcuts_path = shortcuts_path
        self._shortcuts = {}
        self._shortcuts_loaded = False

    async def stop(self) -> None:
        """Unsubscribe from EventBus events and persist pending changes."""
        self._bus.unsubscribe_all(self)
        await self._save_all()

    @staticmethod
    def generate_app_id(launcher: str, identity: str) -> int:
        """Delegate to module-level generate_app_id in games_map.py."""
        return generate_app_id(launcher, identity)

    async def _load_shortcuts(self) -> None:
        """Load shortcuts.vdf into memory (idempotent)."""
        if self._shortcuts_loaded:
            return

        self._shortcuts = await read_vdf(self._shortcuts_path)
        self._shortcuts_loaded = True

    async def _load_games_map(self) -> None:
        """Load games.map with retry-on-corruption (idempotent)."""
        if self._games_map_loaded:
            return

        self._games_map = await read_games_map(self._games_map_path)
        self._games_map_loaded = True

    async def _save_all(self) -> None:
        """Persist shortcuts.vdf + games.map atomically.

        Before writing shortcuts.vdf, re-read the on-disk file and
        merge back any *foreign* shortcut a concurrent writer added
        since our in-memory snapshot was loaded — NonSteamLaunchers'
        scanner service, Steam's shutdown flush, or a manual add. Our
        write is a full read-modify-write of a long-lived cached dict
        (``_shortcuts_loaded`` never re-reads), so without this merge
        it clobbers those entries (UD-043 data loss). Ownership is the
        launcher-``Exe`` gate reconcile already uses: our own entries
        stay memory-authoritative (our deletes are honoured), only
        foreign entries missing from memory are restored.
        """
        if self._shortcuts_loaded:
            disk = await read_vdf(self._shortcuts_path)
            merge_foreign_shortcuts(
                self._shortcuts, disk, self._launcher_path,
            )
            await write_vdf(self._shortcuts_path, self._shortcuts)

        if self._games_map_loaded:
            await write_games_map(self._games_map_path, self._games_map)
