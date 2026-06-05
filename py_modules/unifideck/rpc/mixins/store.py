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

    async def store_auth(self, store: str, action: str) -> Any:
        """Run one step of a store's auth flow.

        Forwards to ``registry.auth_action`` which knows the
        per-store wiring. Every store now authenticates through the
        Steam-shortcut / launcher flow (``"start"`` kicks the backend
        prep; the launcher captures credentials and the session
        monitor emits ``STORE_AUTH_COMPLETE``), so the only actions
        the frontend sends are ``"start"`` and ``"logout"``. The old
        ``"complete"`` + ``{code}`` 2FA path (a relic of Ubisoft's
        former API login) has been removed — Ubisoft Connect handles
        its own sign-in inside the UPC prefix now.

        Args:
            store: store identifier.
            action: per-store action name (``"start"`` / ``"logout"``).

        Returns:
            Per-store auth result dict.
        """
        logger.info("[StoreAuth:%s] action=%s", store, action)
        result = await self.registry.auth_action(store, action)
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
    services: Any

    async def get_real_steam_appid_mappings(self) -> dict[str, Any]:
        """Return ``{shortcut_app_id: real_steam_app_id}`` for every non-Steam game.

        Populated by :class:`MetadataService` on every sync via
        ``fetch_appdetails_for_game``. The frontend
        ``SteamStorePatcher`` reads this map at boot to know which
        synthetic shortcut IDs should be redirected to which real
        Steam Store entries.

        Returns:
            ``{"success": bool, "mappings": {str → int}}``.
        """
        stores = getattr(self.cache, "_stores", None)
        if not isinstance(stores, dict):
            return {"success": False, "mappings": {}}
        store = stores.get("steam_real_appid")
        data = getattr(store, "_data", None)
        if not isinstance(data, dict):
            return {"success": True, "mappings": {}}
        mappings: dict[str, int] = {}
        for k, v in data.items():
            try:
                mappings[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        return {"success": True, "mappings": mappings}

    async def get_steam_metadata_cache(self) -> dict[str, Any]:
        """Return ``{steam_app_id: appdetails_dict}`` for every cached entry.

        The ``appdetails`` dicts are the raw Steam Store JSON
        (description, screenshots, developers, publishers,
        categories, genres, achievements, dlc, controller
        support, platforms, languages). Read by the frontend
        ``SteamStorePatcher`` to spoof non-Steam shortcuts as
        Steam Store games in Steam's UI.

        Returns:
            ``{"success": bool, "metadata": {str → dict}}``.
        """
        stores = getattr(self.cache, "_stores", None)
        if not isinstance(stores, dict):
            return {"success": False, "metadata": {}}
        store = stores.get("steam_metadata")
        data = getattr(store, "_data", None)
        if not isinstance(data, dict):
            return {"success": True, "metadata": {}}
        return {
            "success": True,
            "metadata": {str(k): v for k, v in data.items()},
        }

    async def inject_game_to_appinfo(self, shortcut_app_id: int) -> dict[str, Any]:
        """Persist a Unifideck shortcut's spoofed metadata to ``appinfo.vdf``.

        Persistence layer for the frontend ``SteamStorePatcher``.
        The in-memory patching is enough for the current Steam
        session, but a Steam restart wipes the patched
        ``GetAppOverviewByAppID`` returns. Writing the metadata
        into Steam's ``appinfo.vdf`` makes the spoofing survive
        restarts.

        This is currently a no-op stub — the frontend's
        in-memory patches handle every visible UI element, and
        the on-disk persistence layer requires careful
        ``appinfo.vdf`` parsing that we'd want to vet in a
        separate change. Returns ``success=True`` so the
        frontend's fire-and-forget call doesn't log a failure
        on every navigation.

        Args:
            shortcut_app_id: Unifideck shortcut AppID (synthetic).

        Returns:
            ``{"success": True, "deferred": True, ...}``.
        """
        logger.debug(
            "[StoreRPC] inject_game_to_appinfo(%d) — deferred (in-memory only)",
            shortcut_app_id,
        )
        return {"success": True, "deferred": True, "shortcut_app_id": shortcut_app_id}

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
