"""Artwork event handlers — drive artwork fetching from bus events.

OP-16b | py_modules/unifideck/services/artwork/event_handlers.py

The mixin subscribes the artwork service to four bus events:

* ``GAME_INSTALLED``    — fetch for a single freshly-installed game;
* ``ARTWORK_REQUEST``   — explicit user-triggered fetch (e.g. via
  the QAM "refresh artwork" button), with optional ``force`` flag;
* ``SHORTCUT_CREATED``  — populate the artwork for an auth shortcut
  (special handling because auth shortcuts have no real game title);
* ``SYNC_COMPLETE``     — bulk-fetch on library sync completion,
  parallelised under the service's concurrency semaphore.

Separating the bus wiring from the service class keeps the latter
focused on its synchronous query API (``has``, ``get_url``, etc.)
and the reactive behaviour independently testable.
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
    """Bus subscriptions glued onto ``ArtworkService`` via inheritance."""

    _grid_dir: str

    @subscribe(Events.GAME_INSTALLED)
    async def _on_game_installed(self, **kwargs: Any) -> None:
        """Fetch artwork for a single freshly-installed game.

        Reads ``app_id``, ``store``, ``game_id`` and ``title`` from
        the event payload and delegates to ``fetch_artwork``.
        Silently no-ops if any of the three identifying fields is
        missing (incomplete event from a misbehaving emitter).
        """
        app_id = kwargs.get("app_id")
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        title = kwargs.get("title", "")
        if app_id and store and game_id:
            await self.fetch_artwork(app_id, store, game_id, title)

    @subscribe(Events.ARTWORK_REQUEST)
    async def _on_artwork_request(self, **kwargs: Any) -> None:
        """Honour an explicit ``ARTWORK_REQUEST`` event.

        Skips the fetch when artwork is already present (unless
        ``force=True`` is in the payload, used by the "refresh"
        button to override the cache and re-download). The default
        skip avoids redundant network calls when the UI fires
        repeated requests for the same game.
        """
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
        """Special-case artwork fetch for auth shortcuts.

        Auth shortcuts (Ubisoft Connect, Epic Games Launcher, …)
        don't have a real game title to search SGDB for. This
        handler maps each store identifier to a human-readable
        launcher name (``"Ubisoft Connect"``, ``"Epic Games"``,
        etc.) used as the SGDB query so the launcher's official
        artwork shows up in the Steam library.

        Skips silently if the event isn't for an auth shortcut
        (``is_auth=True`` must be in the payload), if the artwork
        is already present, or if essential fields are missing.
        """
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
        """Bulk-fetch artwork for every game in a sync result.

        Walks the ``games`` payload, filters out entries without
        ``app_id`` or ``title``, skips those with artwork already
        on disk, then launches every remaining fetch in parallel
        through ``asyncio.gather``. Concurrency is bounded by the
        service's internal semaphore (so this gather doesn't open
        500 HTTP connections on a fresh install), and
        ``return_exceptions=True`` keeps a single fetch failure
        from cancelling the others.
        """
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
