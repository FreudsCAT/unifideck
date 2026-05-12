"""Shortcut events mixin — bus emission on shortcut lifecycle.

OP-14b | py_modules/unifideck/services/shortcut/events.py

``EventsMixin`` exposes the helpers used by other mixins to emit bus
events when a shortcut is created, updated, or removed. Centralised
in one mixin so the bus-event schema is owned in a single place.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any
from ...core.types import Events, Game
from ...event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    pass


class EventsMixin:
    """Bus subscriptions glued onto ``ShortcutService``."""

    @subscribe(Events.DOWNLOAD_COMPLETE)
    async def _on_download_complete(self, **kwargs: Any) -> None:
        """Create a Steam shortcut for a freshly-downloaded game.

        Reads the ``game`` payload (a ``Game`` dataclass instance)
        and delegates to ``add_game`` which creates the
        ``shortcuts.vdf`` entry, generates the AppID, and updates
        the games map.

        Silently no-ops on a malformed payload (missing ``game``
        or wrong type) — an incomplete download event from a
        misbehaving emitter shouldn't crash the service.
        """
        game = kwargs.get("game")
        if isinstance(game, Game):
            await self.add_game(game)

    @subscribe(Events.GAME_UNINSTALLED)
    async def _on_game_uninstalled(self, **kwargs: Any) -> None:
        """Remove the Steam shortcut for an uninstalled game.

        Reads the ``app_id`` (Steam-side AppID) from the payload
        and delegates to ``remove_game`` which deletes both the
        ``shortcuts.vdf`` entry and the games-map row.
        """
        app_id = kwargs.get("app_id")
        if isinstance(app_id, int):
            await self.remove_game(app_id)

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """Reconcile shortcuts after a full library sync.

        After a sync, some games may have been added by other
        store-side tools (the user installing through the store
        client directly) and others removed. ``reconcile`` walks
        the new game list and adds/removes shortcuts to match,
        keeping the Steam library coherent with what the store
        reports.
        """
        games = kwargs.get("games", [])
        if games:
            await self.reconcile(games)
