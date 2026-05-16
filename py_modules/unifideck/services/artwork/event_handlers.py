"""services/artwork/event_handlers.py — EventBus subscribers.

4 ``@subscribe``-decorated handlers driving the artwork
pipeline. All ultimately call ``self.fetch_artwork`` on the
host; they differ in trigger signals and payload shapes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events
from unifideck.event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from unifideck.core.types import Game
    # This is a mixin; `self` will be the ArtworkService facade at runtime.

logger = logging.getLogger(__name__)

# Strong references to background fetch tasks so the GC can't
# collect them mid-flight (see RUF006). Tasks remove themselves on
# completion via ``add_done_callback``.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _track(task: asyncio.Task[Any]) -> None:
    """Register a fire-and-forget task so the GC doesn't collect it early."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

# Store id → SteamGridDB title for auth shortcuts. SGDB has art
# for "Amazon Games", not for "amazon" or "Amazon Games Sign-In".
# Reference data — kept here so the auth-shortcut handler stays
# short and the table is greppable from anywhere.
_AUTH_TITLE_FOR_LOOKUP: dict[str, str] = {
    "amazon": "Amazon Games",
    "epic": "Epic Games",
    "gog": "GOG Galaxy",
    "microsoft": "Xbox",
    "ubisoft": "Ubisoft Connect",
}


class _EventHandlersMixin:
    """EventBus subscribers for artwork fetching."""

    # Handlers assume host provides fetch_artwork
    # async def fetch_artwork(self, app_id: int, store: str, game_id: str, title: str) -> dict: ...

    @subscribe(Events.GAME_INSTALLED)
    async def _on_game_installed(self: Any, **kwargs: Any) -> None:
        """Fetch artwork immediately after a new install.

        Missing ``app_id``/``store``/``game_id`` → silent skip
        (partial payloads happen when the emitter failed to
        resolve one of the fields).
        """
        app_id = kwargs.get("app_id")
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        title = kwargs.get("title")

        if not all((app_id, store, game_id, title)):
            return

        # Fire and forget; background task
        _track(asyncio.create_task(self.fetch_artwork(app_id, store, game_id, title)))

    @subscribe(Events.ARTWORK_REQUEST)
    async def _on_artwork_request(self: Any, **kwargs: Any) -> None:
        """Handle on-demand artwork fetch requests.

        Contract: ``app_id`` + ``title`` required. ``force=True``
        bypasses the "already has artwork" check (useful on
        account switch when existing art is stale). ``store`` /
        ``game_id`` optional — SteamGridDB only needs the title.
        """
        app_id = kwargs.get("app_id")
        title = kwargs.get("title")
        force = kwargs.get("force", False)
        store = kwargs.get("store", "unknown")
        game_id = kwargs.get("game_id", "unknown")

        if not app_id or not title:
            return

        _track(asyncio.create_task(
            self.fetch_artwork(app_id, store, game_id, title, force=force)
        ))

    @subscribe(Events.SHORTCUT_CREATED)
    async def _on_shortcut_created(self: Any, **kwargs: Any) -> None:
        """Fetch a cover for a newly-created shortcut.

        Only acts on auth shortcuts (``is_auth=True``) — game
        shortcuts already get artwork via ``GAME_INSTALLED``
        with richer data. Uses ``_AUTH_TITLE_FOR_LOOKUP`` to
        map the store id to what SGDB actually has art for.
        """
        is_auth = kwargs.get("is_auth", False)
        if not is_auth:
            return

        app_id = kwargs.get("app_id")
        store = kwargs.get("store")
        title = kwargs.get("title")

        if not app_id or not store:
            return

        sgdb_title = _AUTH_TITLE_FOR_LOOKUP.get(store, title or store)

        _track(asyncio.create_task(
            self.fetch_artwork(app_id, store, "auth", sgdb_title)
        ))

    @subscribe(Events.SYNC_COMPLETE)
    async def _on_sync_complete(self: Any, **kwargs: Any) -> None:
        """Bulk-fetch artwork for every synced game missing art.

        All fetches run concurrently via ``asyncio.gather`` —
        rate limiting comes from the semaphore inside
        ``fetch_artwork``. ``return_exceptions=True`` so one
        failure doesn't block the batch.
        """
        games = kwargs.get("games", [])
        if not games:
            return

        from .fetcher import has_artwork

        async def _process_game(game: Game) -> None:
            if not game.exe_path:
                return

            from unifideck.services.shortcut.games_map import generate_app_id
            app_id = generate_app_id(game.exe_path, game.title)

            if not getattr(self, "_grid_dir", None):
                return

            has_art = await has_artwork(self._grid_dir, app_id)
            if not has_art:
                await self.fetch_artwork(app_id, game.store, game.app_id, game.title)

        # Launch all fetches. The semaphore inside fetch_artwork controls concurrency.
        tasks: list[Any] = [_process_game(game) for game in games]
        if tasks:
            # asyncio.gather() returns a Future, not a Coroutine — so
            # ensure_future() is the right wrapper here (it accepts
            # either and returns a Task). create_task() rejects Futures
            # under mypy strict.
            _track(asyncio.ensure_future(
                asyncio.gather(*tasks, return_exceptions=True),
            ))
