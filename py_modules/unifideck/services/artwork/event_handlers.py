"""Artwork event-handlers mixin — react to library changes.

OP-16b | py_modules/unifideck/services/artwork/event_handlers.py

``_EventHandlersMixin`` subscribes to bus events that should trigger
artwork (re-)fetch :

* ``game_added`` — ensure artwork for the new entry;
* ``game_uninstalled`` — keep artwork (user may re-install);
* ``library_reset`` — drop all artwork.

Decoupling the event subscription from the service's public API
keeps the API focused on synchronous querying and the reactive
behaviour testable in isolation.
"""

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING, Any
from ...core.types import Events
from ...event_bus.event_bus_devex import subscribe
from .fetcher import has_artwork

if TYPE_CHECKING:
    from ...core.types import Game
logger = logging.getLogger(__name__)


class _EventHandlersMixin:
    """Event handlers mixin."""

    _grid_dir: str

    @subscribe(Events.GAME_INSTALLED)
    async def _on_game_installed(self, **kwargs: Any) -> None:
        """On game installed."""
        app_id = kwargs.get("app_id")
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        title = kwargs.get("title", "")
        if app_id and store and game_id:
            await self.fetch_artwork(app_id, store, game_id, title)

    @subscribe(Events.ARTWORK_REQUEST)
    async def _on_artwork_request(self, **kwargs: Any) -> None:
        """On artwork request."""
        app_id = kwargs.get("app_id")
        title = kwargs.get("title", "")
        if not app_id or not title:
            logger.debug(
                "[ArtworkService] ARTWORK_REQUEST ignored: missing app_id or title",
            )
            return
        force = bool(kwargs.get("force", False))
        if not force and await has_artwork(self._grid_dir, app_id):
            logger.debug(
                "[ArtworkService] artwork already present for app_id=%d, skipping",
                app_id,
            )
            return
        store = kwargs.get("store", "")
        game_id = kwargs.get("game_id", "")
        await self.fetch_artwork(app_id, store, game_id, title)

    @subscribe(Events.SHORTCUT_CREATED)
    async def _on_shortcut_created(self, **kwargs: Any) -> None:
        """On shortcut created."""
        if not kwargs.get("is_auth"):
            return
        unsigned_id = kwargs.get("unsigned_id")
        store = kwargs.get("store", "")
        if not unsigned_id or not store:
            return
        title_for_lookup = {
            "amazon": "Amazon Games",
            "epic": "Epic Games",
            "gog": "GOG Galaxy",
            "microsoft": "Xbox",
            "ubisoft": "Ubisoft Connect",
        }.get(store, store.capitalize())
        if not await has_artwork(self._grid_dir, unsigned_id):
            await self.fetch_artwork(
                unsigned_id,
                store,
                f"{store}-auth",
                title_for_lookup,
            )

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self, **kwargs: Any) -> None:
        """On sync complete."""
        games: list[Game] = kwargs.get("games", [])
        tasks = []
        for game in games:
            if not game.app_id or not game.title:
                continue
            if await has_artwork(self._grid_dir, game.app_id):
                continue
            tasks.append(
                self.fetch_artwork(
                    game.app_id,
                    game.store,
                    game.store_game_id,
                    game.title,
                )
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
