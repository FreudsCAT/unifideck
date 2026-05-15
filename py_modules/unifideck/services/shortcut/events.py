"""services/shortcut/events.py — Event handlers for shortcut lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events, Game
from unifideck.event_bus.event_bus_devex import subscribe

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
        """Reconcile shortcuts against the new library state."""
        games = kwargs.get("games", [])
        if games:
            await self.reconcile(games)
