"""services/shortcut/events.py — Event handlers for shortcut lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...core.types import Events, Game
from ...event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    # This is a mixin; `self` will be the ShortcutService facade at runtime.
    pass


class EventsMixin:
    """Event subscriptions for ShortcutService."""

    @subscribe(Events.DOWNLOAD_COMPLETE)
    async def _on_download_complete(self, **kwargs: Any) -> None:
        """Add shortcut when a download finishes successfully."""
        game = kwargs.get("game")
        if isinstance(game, Game):
            await self.add_game(game)

    @subscribe(Events.GAME_UNINSTALLED)
    async def _on_game_uninstalled(self, **kwargs: Any) -> None:
        """Remove shortcut when a game is uninstalled."""
        app_id = kwargs.get("app_id")
        if isinstance(app_id, int):
            await self.remove_game(app_id)

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """Reconcile shortcuts against the new library state."""
        games = kwargs.get("games", [])
        if games:
            await self.reconcile(games)
