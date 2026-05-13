"""StoreHandlers — store auth, library sync, install/uninstall RPC.

OP-25g | py_modules/unifideck/rpc/handlers/store_handlers.py

The largest handler group, covering the four main store
interactions surfaced to the frontend:

* **Authentication** — start/continue/cancel auth flows,
  logout all stores, check per-store login status.
* **Library** — read the unified games list, get per-game
  details, trigger background syncs (normal or forced) and
  watch their progress.
* **Lifecycle** — install / uninstall / check-for-update on
  a specific (store, game_id) pair.

Most methods delegate either to ``StoreRegistry`` (for
per-store operations) or to ``SyncService`` (for the
multi-store sync orchestration). Per-store delegation uses
``get_store`` + a ``store_not_found`` typed error to surface
typos / misconfigurations clearly.
"""

from __future__ import annotations

from typing import Any, cast

from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.wrapper import RpcError


class StoreHandlers(RpcHandlerBase):
    """Store-side RPC surface — auth, library, install lifecycle."""

    async def store_auth(self, store: str, action: str, **kw: Any) -> Any:
        """Run one step of a store's auth flow.

        Forwards directly to ``registry.auth_action`` which
        knows the per-store wiring. The ``action`` argument
        is store-defined (typically ``"start"`` /
        ``"continue"`` / ``"cancel"`` / ``"check"``).

        Args:
            store: store identifier.
            action: per-store action name.
            **kw: extra args forwarded to the auth method.

        Returns:
            Per-store auth result dict.
        """
        return cast(dict, await self._registry.auth_action(store, action, **kw))

    async def check_store_status(self) -> Any:
        """Probe every registered store for its current auth/login state.

        Used by the stores tab to render the per-store
        login-status badges. The registry parallelises the
        probes internally so this stays fast even with
        five stores.

        Returns:
            List of per-store status dicts.
        """
        return cast(list, await self._registry.check_all_status())

    async def get_store_infos(self) -> Any:
        """Return the static metadata (id, name, icon, capabilities) per store.

        Synchronous on the registry side — pulled from the
        bundled store descriptors at registration time.

        Returns:
            List of store-info dicts.
        """
        return cast(list, self._registry.get_store_infos())

    async def clear_store_auths(self) -> Any:
        """Sign out of every store and wipe cached credentials.

        Loud admin action: requires user confirmation in
        the UI. Delegates to ``registry.logout_all`` which
        iterates and calls each store's logout method.

        Returns:
            Per-store outcome dict from
            ``registry.logout_all``.
        """
        return cast(dict, await self._registry.logout_all())

    async def sync_libraries(self, **kw: Any) -> Any:
        """Trigger a multi-store library sync, awaiting completion.

        Unlike ``ActionHandlers._handle_refresh_*`` which
        schedules in the background and returns immediately,
        this method awaits the sync — so the frontend can
        show a "syncing…" spinner and learn the outcome.

        Args:
            **kw: optional sync tunables forwarded to
                ``SyncService.sync_all``.

        Returns:
            Sync-outcome dict from ``sync_all``.
        """
        return cast(dict, await self._sync.sync_all(**kw))

    async def force_sync_libraries(self, **kw: Any) -> Any:
        """Like ``sync_libraries`` but bypasses per-store cache TTLs.

        Used when the user clicks "force refresh" — useful
        when a store's cache hasn't expired but the library
        is known to have changed (e.g. just claimed a free
        weekly game).

        Args:
            **kw: forwarded with ``force=True`` added.

        Returns:
            Sync-outcome dict.
        """
        return cast(dict, await self._sync.sync_all(force=True, **kw))

    async def get_sync_status(self) -> Any:
        """Return whether a sync is in progress + last-sync metadata.

        Args:
            (none)

        Returns:
            ``{is_syncing, last_sync_ts, per_store: {...}}``
            dict from ``SyncService.get_status``.
        """
        return cast(dict, self._sync.get_status())

    async def get_sync_progress(self) -> Any:
        """Return the same payload as ``get_sync_status`` (alias).

        Kept for backward compatibility with the frontend's
        progress poller — at one point the two endpoints
        had different shapes; they've since converged.

        Returns:
            Same dict as ``get_sync_status``.
        """
        return cast(dict, self._sync.get_status())

    async def cancel_sync(self) -> Any:
        """Request cancellation of any in-flight sync.

        The cancel is cooperative — each store checks the
        cancel flag at safe points and stops at the next
        opportunity. The returned dict reports whether a
        sync was actually in flight to be cancelled.

        Returns:
            ``{cancelled: bool, ...}`` dict from
            ``SyncService.cancel``.
        """
        return cast(dict, await self._sync.cancel())

    async def get_all_unifideck_games(self) -> Any:
        """Return the unified list of games across every store.

        Used by the main library view. Synchronous on the
        sync-service side because the unified list is kept
        in memory after each sync.

        Returns:
            List of game dicts (cross-store schema).
        """
        return cast(list, self._sync.get_all_games())

    async def get_game_info(self, app_id: int) -> Any:
        """Return the full record for a single Steam AppID.

        The AppID is the Unifideck-derived one (deterministic
        from store+game_id+title), so the frontend can
        cross-reference it against Steam's library directly.

        Args:
            app_id: Steam-style AppID.

        Returns:
            Game info dict, or empty dict when unknown.
        """
        return cast(dict, self._sync.get_game_info(app_id))

    async def install_game(self, store: str, game_id: str, **kw: Any) -> Any:
        """Trigger a store-side install for a specific game.

        Per-store delegation: the registry resolves the
        store, then forwards to its ``install_game`` method.
        Typically this enqueues the install rather than
        running it inline (the download service handles
        sequencing).

        Args:
            store: store identifier.
            game_id: store-specific game id.
            **kw: install options (path, language, etc.)
                forwarded to the store.

        Returns:
            ``Result`` dict from the store's installer.

        Raises:
            RpcError: ``store_not_found`` when the store id
                doesn't match any registered store.
        """
        s = self._registry.get_store(store)
        if s is None:
            raise RpcError("store_not_found", store=store)
        return cast(dict, await s.install_game(game_id, **kw))

    async def uninstall_game(self, store: str, game_id: str) -> Any:
        """Trigger a store-side uninstall for a specific game.

        Same per-store delegation pattern as
        ``install_game``. Some stores can't actually
        uninstall (e.g. Microsoft Game Pass streaming),
        in which case they return a typed error dict.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``Result`` dict from the store's uninstaller.

        Raises:
            RpcError: ``store_not_found`` on unknown store.
        """
        s = self._registry.get_store(store)
        if s is None:
            raise RpcError("store_not_found", store=store)
        return cast(dict, await s.uninstall_game(game_id))

    async def check_game_update(self, store: str, game_id: str) -> Any:
        """Ask the store whether a game has an update available.

        Used by the per-game UI to show an "update
        available" badge. Per-store implementations vary —
        some compare local manifests, others poll a
        version API.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``{has_update: bool, ...}`` dict, store-specific
            extras may include version strings or download
            size.

        Raises:
            RpcError: ``store_not_found`` on unknown store.
        """
        s = self._registry.get_store(store)
        if s is None:
            raise RpcError("store_not_found", store=store)
        return cast(dict, await s.check_game_update(game_id))
