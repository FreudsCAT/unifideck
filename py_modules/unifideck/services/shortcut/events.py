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
    """Events mixin."""

    @subscribe(Events.DOWNLOAD_COMPLETE)
    async def _on_download_complete(self, **kwargs: Any) -> None:
        """On download complete."""
        game = kwargs.get("game")
        if isinstance(game, Game):
            await self.add_game(game)

    @subscribe(Events.GAME_UNINSTALLED)
    async def _on_game_uninstalled(self, **kwargs: Any) -> None:
        """On game uninstalled."""
        app_id = kwargs.get("app_id")
        if isinstance(app_id, int):
            await self.remove_game(app_id)

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """On sync complete."""
        games = kwargs.get("games", [])
        if games:
            await self.reconcile(games)
