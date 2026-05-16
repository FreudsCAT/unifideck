"""StoreRPCMixin — auth + login-state RPC (subset of StoreHandlers).

OP-26e | py_modules/unifideck/rpc/mixins/store.py

Mixin form of the auth-related slice of ``StoreHandlers``
(OP-25g). Where the handler group covers auth + library +
sync + install, this mixin only covers the auth surface —
the rest lived in ``SyncRPCMixin`` historically.

Auth-shortcut context RPCs (``get_<store>_auth_shortcut_context``
+ ``get_compat_tool_for_game``) live in a sibling
``AuthShortcutsRPCMixin`` (OP-26k) to keep this file under the
200 LOC ceiling.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


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
        logger.info(
            "[StoreAuth:%s] action=%s kw=%s", store, action,
            {k: v for k, v in kw.items() if k != "code"},
        )
        result = await self.registry.auth_action(store, action, **kw)
        success = getattr(result, "success", None)
        if success is None and isinstance(result, dict):
            success = result.get("success")
        error = getattr(result, "error", None)
        if error is None and isinstance(result, dict):
            error = result.get("error")
        logger.info(
            "[StoreAuth:%s] action=%s success=%s error=%s",
            store, action, success, error,
        )
        return result

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

    cache: Any

    async def get_protondb_cache(self) -> dict[str, Any]:
        """Return every cached ProtonDB / Deck-Verified entry.

        Used by the frontend ``protondb-cache`` module to populate the
        in-memory rating lookup that drives compat badges and the
        ``deckCompat`` library-tab filter. Reads the ``compat`` cache
        namespace populated by :class:`CompatLibrary` — never triggers
        a fresh network fetch from here.

        Returns:
            Mapping of ``str(app_id)`` →
            ``{"protondb_tier": str | None, "deck_status": str,
              "title": str, "sources": list[str]}``.
            Empty dict when the cache is cold or unregistered.
        """
        stores = getattr(self.cache, "_stores", None)
        if not isinstance(stores, dict):
            return {}
        compat_store = stores.get("compat")
        data = getattr(compat_store, "_data", None)
        if not isinstance(data, dict):
            return {}
        return dict(data)
