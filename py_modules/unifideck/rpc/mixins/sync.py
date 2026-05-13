"""SyncRPCMixin — library sync + game info RPC.

OP-26h | py_modules/unifideck/rpc/mixins/sync.py

Mixin equivalent of the sync-related slice of ``StoreHandlers``
(OP-25g). Sync orchestration was historically split into its
own mixin separate from auth.

API differences vs the handler group:

* The mixin reaches for ``self.sync_service`` directly (older
  composition style).
* ``sync_libraries`` calls ``sync_service.sync(**kw)`` —
  legacy method name — vs the handler group's ``sync_all``.
* ``get_sync_progress`` has its own implementation
  (``sync_service.get_progress``) whereas the handler group
  aliased it to ``get_status``.
"""

from __future__ import annotations

from typing import Any


class SyncRPCMixin:
    """Library-sync trigger/observe + games-list reads RPC."""

    sync_service: Any

    async def sync_libraries(self, **kw: Any) -> Any:
        """Trigger a multi-store library sync, awaiting completion.

        Unlike the action-mixin's ``refresh-library`` /
        ``refresh-all-libraries`` verbs which fire-and-forget,
        this method awaits the sync so the frontend can show
        a "syncing…" spinner and learn the outcome.

        Args:
            **kw: optional sync tunables forwarded to
                ``sync_service.sync``.

        Returns:
            Sync-outcome dict from the sync service.
        """
        return await self.sync_service.sync(**kw)

    async def force_sync_libraries(self, **kw: Any) -> Any:
        """Like ``sync_libraries`` but bypasses per-store cache TTLs.

        Used for "force refresh" — when the cache hasn't
        expired but the library is known to have changed.

        Args:
            **kw: forwarded with ``force=True`` added.

        Returns:
            Sync-outcome dict.
        """
        return await self.sync_service.sync(force=True, **kw)

    async def get_sync_status(self) -> Any:
        """Return whether a sync is in progress + last-sync metadata.

        Synchronous read of the in-memory state — no awaits
        beyond the asyncio coroutine boilerplate.

        Returns:
            ``{is_syncing, last_sync_ts, per_store: {...}}``
            dict from ``sync_service.get_status``.
        """
        return self.sync_service.get_status()

    async def get_sync_progress(self) -> Any:
        """Return live progress numbers from an in-flight sync.

        Distinct from ``get_sync_status`` in this mixin
        (the handler-group version aliases the two). Used
        by the frontend's progress bar.

        Returns:
            ``{progress: float, current_store, current_step,
            ...}`` dict from ``sync_service.get_progress``.
        """
        return self.sync_service.get_progress()

    async def cancel_sync(self) -> Any:
        """Request cancellation of any in-flight sync.

        Cooperative cancel — each store checks the cancel
        flag at safe points. The returned dict reports
        whether a sync was actually in flight to be
        cancelled.

        Returns:
            ``{cancelled: bool, ...}`` dict from
            ``sync_service.cancel``.
        """
        return await self.sync_service.cancel()

    async def get_all_unifideck_games(self) -> Any:
        """Return the unified list of games across every store.

        Used by the main library view. Async on this older
        API (the handler-group version reads from memory
        synchronously).

        Returns:
            List of game dicts (cross-store schema).
        """
        return await self.sync_service.get_all_games()

    async def get_game_info(self, app_id: int) -> Any:
        """Return the full record for a single Unifideck AppID.

        Args:
            app_id: Steam-style AppID (deterministic from
                store + game_id + title).

        Returns:
            Game info dict, or empty / None when unknown.
        """
        return await self.sync_service.get_game_info(app_id)
