"""services/shortcut/events.py — Event handlers for shortcut lifecycle."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events, Game
from unifideck.event_bus.event_bus_devex import subscribe

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # This is a mixin; `self` will be the ShortcutService facade
    # at runtime. The facade provides ``add_game``, ``remove_game``
    # and ``reconcile`` via ``_GamesMapMixin``. Mypy doesn't see
    # the multiple-inheritance composition here (this file is
    # imported standalone), so we declare the protocol of methods
    # we rely on as TYPE_CHECKING-only forward refs.
    from collections.abc import Sequence


class EventsMixin:
    """Event subscriptions for ShortcutService.

    Expects the composing class to provide the methods listed
    in the ``if TYPE_CHECKING`` block below. Each handler reads
    its payload from the bus and delegates to the facade.
    """

    if TYPE_CHECKING:
        # Type-only declarations — implementations come from
        # ``_GamesMapMixin`` at runtime through the MRO. These
        # stubs exist purely so mypy knows the methods exist on
        # ``self`` when this module is type-checked in isolation.
        #
        # Signatures match ``_GamesMapMixin`` exactly: ``async def
        # foo(...) -> T`` (not ``def foo(...) -> Awaitable[T]``).
        # Lot 12d fix: previously the stubs returned ``Awaitable[T]``
        # which is semantically equivalent but mypy strict considered
        # them incompatible with the ``async def`` definitions in
        # ``_GamesMapMixin`` — surfaced as 3× ``[misc]`` "incompatible
        # definition in base class" errors on the facade class
        # body in service.py.
        async def add_game(self, game: Game) -> int: ...
        async def remove_game(self, app_id: int) -> bool: ...
        async def reconcile(
            self, games: Sequence[Game],
        ) -> dict[str, int]: ...

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
        """Reconcile shortcuts against the new library state.

        After reconciling, emit ``SHORTCUT_RECONCILE_COMPLETE``
        with the per-batch counters so the frontend can prompt
        the user for a Steam restart when any shortcuts were
        added or removed (Steam holds shortcuts.vdf in memory and
        overwrites our writes on its next shutdown otherwise).
        """
        games = kwargs.get("games", [])
        if not games:
            return
        logger.info(
            "[ShortcutService] SYNC_COMPLETE → reconciling %d games",
            len(games),
        )
        result = await self.reconcile(games)
        added = result.get("added", 0)
        removed = result.get("removed", 0)
        kept = result.get("kept", 0)
        # ``self._bus`` is provided by the host (ShortcutService
        # facade); silently skip the emit if for some reason it's
        # unavailable so a missing bus never breaks reconcile.
        bus = getattr(self, "_bus", None)
        if bus is None:
            return
        await bus.emit(
            Events.SHORTCUT_RECONCILE_COMPLETE,
            added=added, removed=removed, kept=kept,
            total=len(games),
        )
