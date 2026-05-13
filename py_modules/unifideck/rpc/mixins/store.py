"""StoreRPCMixin — auth + login-state RPC (subset of StoreHandlers).

OP-26e | py_modules/unifideck/rpc/mixins/store.py

Mixin form of the auth-related slice of ``StoreHandlers``
(OP-25g). Where the handler group covers auth + library +
sync + install, this mixin only covers the auth surface —
the rest lived in ``SyncRPCMixin`` historically.
"""

from __future__ import annotations

from typing import Any


class StoreRPCMixin:
    """Store-auth RPC: start/check/clear flows + login status."""

    registry: Any

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
        return await self.registry.auth_action(store, action, **kw)

    async def check_store_status(self) -> Any:
        """Probe every registered store for its current login state.

        Used by the stores tab to render the per-store
        login-status badges. The registry parallelises the
        probes internally.

        Returns:
            List of per-store status dicts.
        """
        return await self.registry.check_all_status()

    async def get_store_infos(self) -> Any:
        """Return the static metadata (id, name, icon) for every store.

        Synchronous on the registry side — pulled from the
        bundled store descriptors at registration time.

        Returns:
            List of store-info dicts.
        """
        return self.registry.get_store_infos()

    async def clear_store_auths(self) -> Any:
        """Sign out of every store and wipe cached credentials.

        Loud admin action: requires user confirmation in
        the UI. Delegates to ``registry.logout_all`` which
        iterates and calls each store's logout method.

        Returns:
            Per-store outcome dict.
        """
        return await self.registry.logout_all()
