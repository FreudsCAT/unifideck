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


def _log_batch_result(
    future: asyncio.Future[list[Any]], label: str,
) -> None:
    """Log a completion summary for a batch artwork / metadata gather."""
    if future.cancelled():
        logger.info("%s batch was cancelled", label)
        return
    results = future.result()
    downloaded = sum(1 for r in results if r == "cover-saved")
    existing = sum(1 for r in results if r == "cover-exists")
    no_match = sum(1 for r in results if r == "no-cover-found")
    skipped = sum(1 for r in results if r == "skipped")
    exc_count = sum(1 for r in results if isinstance(r, BaseException))
    logger.info(
        "%s artwork batch finished: %d covers saved, %d already on disk, "
        "%d no match, %d skipped, %d errors — %d total",
        label, downloaded, existing, no_match, skipped, exc_count, len(results),
    )
    if exc_count:
        for r in results:
            if isinstance(r, BaseException):
                logger.warning(
                    "%s artwork fetch error: %s: %s",
                    label, type(r).__name__, r,
                )


def _on_artwork_batch_done(
    future: asyncio.Future[list[Any]], bus: Any,
) -> None:
    """Done callback: log the batch result + emit POST_SYNC_PHASE_CHANGED."""
    _log_batch_result(future, "[ArtworkService]")
    total = len(future.result() if not future.cancelled() else [])
    if bus is not None:
        _track(asyncio.ensure_future(bus.emit(
            Events.POST_SYNC_PHASE_CHANGED,
            phase="artwork", active=False, total=total, done=total,
        )))

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

        Fires once per sync, spawns a background batch that runs
        every game through ``fetch_artwork`` concurrently (rate-
        limited by the semaphore inside the service). Progress
        and completion are logged so the Decky log shows forward
        motion on large libraries.

        Emits ``POST_SYNC_PHASE_CHANGED`` on start / completion
        so the frontend progress bar stays alive through the
        artwork-download phase.
        """
        games = kwargs.get("games", [])
        if not games:
            return
        grid_dir = getattr(self, "_grid_dir", None)
        if not grid_dir:
            logger.warning(
                "[ArtworkService] _grid_dir unset — cannot save covers",
            )
            return
        bus = getattr(self, "_bus", None)
        total = len(games)
        logger.info(
            "[ArtworkService] SYNC_COMPLETE → checking artwork "
            "for %d games (grid_dir=%s)",
            total, grid_dir,
        )
        tasks: list[Any] = [
            self._process_one_game(g, grid_dir, bus) for g in games
        ]
        if not tasks:
            return
        fut = asyncio.ensure_future(
            asyncio.gather(*tasks, return_exceptions=True),
        )
        fut.add_done_callback(
            lambda f: _on_artwork_batch_done(f, bus)
        )
        _track(fut)

    async def _process_one_game(
        self: Any, game: Game, grid_dir: str, bus: Any,
    ) -> str:
        """Resolve artwork for a single game; return a status tag.

        Returns ``"cover-saved"``, ``"cover-exists"``,
        ``"no-cover-found"``, or ``"skipped"``. Calls
        ``increment_artwork`` on the shared ``SyncProgress``
        instance (via ``bus.get_sync_progress()``) so the
        frontend progress bar ticks up per game — mirroring
        staging's ``sync_progress.increment_artwork()`` pattern.
        """
        from .fetcher import has_artwork

        if not game.app_id or not game.title:
            return "skipped"
        if await has_artwork(grid_dir, game.app_id):
            return "cover-exists"
        extras = getattr(game, "metadata", None)
        result = await self.fetch_artwork(
            game.app_id, game.store, game.store_game_id, game.title,
            extras=extras,
        )
        # Tick the progress bar — SyncService puts the tracker
        # on the bus in _setup_sync and clears it on completion.
        if bus is not None:
            progress = bus.get_sync_progress() if hasattr(bus, "get_sync_progress") else None
            if progress is not None:
                await progress.increment_artwork(game.title)
        return "cover-saved" if any(result.values()) else "no-cover-found"
