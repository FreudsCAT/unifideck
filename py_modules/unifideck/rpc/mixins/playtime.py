"""PlaytimeRPCMixin — per-game and global playtime stats RPC.

OP-26d | py_modules/unifideck/rpc/mixins/playtime.py

Mixin equivalent of ``LaunchHandlers.get_playtime`` /
``get_all_playtimes`` (OP-25d). Two read-only methods over the
playtime service.

Note: the method names on the service are ``get`` / ``get_all``
here (older API), whereas the handler-group version uses
``get_playtime`` / ``get_all_playtimes`` — the mixin predates
the rename and stays on the legacy names for compatibility.
"""

from __future__ import annotations

from typing import Any


class PlaytimeRPCMixin:
    """Playtime read RPC — per-game + global aggregations."""

    services: Any

    async def get_playtime(self, store: str, game_id: str) -> Any:
        """Return aggregated playtime for one game.

        Delegates to ``services.playtime.get`` (the legacy
        method name on the playtime service).

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            Per-game playtime dict from the service.
        """
        return await self.services.playtime.get(store, game_id)

    async def get_all_playtimes(self) -> Any:
        """Return aggregated playtime across every tracked game.

        Returns:
            List of per-game playtime dicts, typically
            ordered newest-first by last-played timestamp.
        """
        return await self.services.playtime.get_all()
