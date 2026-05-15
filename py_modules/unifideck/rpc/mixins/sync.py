"""Sync RPC mixin for Plugin class.

OP-26f | rpc/mixins/sync.py
"""
from __future__ import annotations

from typing import Any


class SyncRPCMixin:
    """Library sync, progress, and game queries."""

    sync_service: Any

    async def sync_libraries(self, **kw: Any) -> Any:
        """Trigger a full library sync across every store.

        The underlying service method is ``sync_all`` (an earlier
        version called ``sync`` which doesn't exist on
        :class:`SyncService` — the RPC raised ``AttributeError``).
        """
        return await self.sync_service.sync_all(**kw)

    async def force_sync_libraries(
        self, resync_artwork: bool = False, **kw: Any,
    ) -> Any:
        """Like ``sync_libraries`` but bypasses per-store cache TTLs.

        Used for "force refresh" — when the cache hasn't
        expired but the library is known to have changed.

        Args:
            resync_artwork: whether to re-fetch all artwork
                (passed from the frontend's ForceSyncModal).
            **kw: forwarded with ``force=True`` added.

        Returns:
            Sync-outcome dict.
        """
        return await self.sync_service.sync_all(force=True, **kw)

    async def get_sync_status(self) -> Any:
        """Return whether a sync is running + last completion time."""
        return self.sync_service.get_status()

    async def get_sync_progress(self) -> Any:
        """Return per-store progress during an in-flight sync.

        Progress is bundled into ``get_status`` — there is no
        separate ``get_progress`` on :class:`SyncService`.
        """
        return self.sync_service.get_status()

    async def cancel_sync(self) -> Any:
        """Cancel an in-flight sync."""
        return await self.sync_service.cancel()

    async def get_all_unifideck_games(self) -> Any:
        """Return every known game across every store.

        :meth:`SyncService.get_all_games` is synchronous; an
        earlier version awaited it and crashed with
        ``TypeError: object list can't be used in 'await' expression``.
        """
        return self.sync_service.get_all_games()

    async def get_game_info(self, app_id: int) -> Any:
        """Return the full record for a single Unifideck AppID.

        Args:
            app_id: Steam-style AppID (deterministic from
                store + game_id + title).

        Returns:
            Game info dict, or empty / None when unknown.
        """
        # `sync_service.get_game_info` is a synchronous helper
        # (linear scan over `_all_games`) — no coroutine, no
        # await. The previous body had a stray `await` which
        # raised `TypeError: object NoneType can't be used
        # in 'await' expression`.
        return self.sync_service.get_game_info(app_id)
