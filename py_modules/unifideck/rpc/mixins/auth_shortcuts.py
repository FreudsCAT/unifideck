"""AuthShortcutsRPCMixin — per-store auth-shortcut context RPCs.

OP-26k | py_modules/unifideck/rpc/mixins/auth_shortcuts.py

Each registered store has a dedicated Steam shortcut that the
frontend launches via ``SteamClient.Apps.RunGame`` to drive the
auth handshake (browser inside a Wine prefix, CLI in a temp
prefix, etc.). The frontend launchers in
``utils/authShortcutLaunch.ts`` need :

* the **appid** of the persistent shortcut (so they can find
  it in Steam's in-memory state), and
* the **launcher path** to fall back to when the persistent
  shortcut isn't loaded yet (so they can create a temp
  shortcut against the actual ``bin/unifideck-launcher``
  wrapper).

This mixin returns that metadata for each store. The
deterministic appid mirrors what ``ShortcutService.add_auth_shortcut``
wrote into ``shortcuts.vdf`` so both sides agree.

Ubisoft has its own VDF-scan + repair logic in
``UbisoftStore.get_auth_shortcut_context`` ; we just proxy.

Lives in its own file (split from ``StoreRPCMixin``) so each
mixin honours the 200 LOC ceiling.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Default delay used when the frontend has to wait for Steam to
# load the freshly-created shortcut into in-memory state. Lifted
# from the legacy ``waitForShortcut`` timeout in
# ``authShortcutLaunch.ts``.
_AUTH_SHORTCUT_LAUNCH_WAIT_MS = 5000

# Per-store metadata used to compute the auth-shortcut context.
# The title must match what the store object passes to
# ``add_auth_shortcut`` in ``_ensure_auth_shortcut`` so
# ``generate_app_id`` returns the same id on both sides.
_AUTH_SHORTCUT_META: dict[str, dict[str, str]] = {
    "epic":      {"title": "Epic Games Sign-In",   "env": "UNIFIDECK_EPIC_ACTION"},
    "gog":       {"title": "GOG Sign-In",          "env": "UNIFIDECK_GOG_ACTION"},
    "amazon":    {"title": "Amazon Games Sign-In", "env": "UNIFIDECK_AMAZON_ACTION"},
    "microsoft": {"title": "Microsoft Sign-In",    "env": "UNIFIDECK_MICROSOFT_ACTION"},
}


class AuthShortcutsRPCMixin:
    """Per-store auth-shortcut context + compat-tool lookup."""

    registry: Any

    async def get_epic_auth_shortcut_context(self) -> Any:
        """Auth-shortcut context for the Epic Games launcher."""
        return _build_and_log("epic")

    async def get_gog_auth_shortcut_context(self) -> Any:
        """Auth-shortcut context for the GOG launcher."""
        return _build_and_log("gog")

    async def get_amazon_auth_shortcut_context(self) -> Any:
        """Auth-shortcut context for the Amazon Games launcher."""
        return _build_and_log("amazon")

    async def get_microsoft_auth_shortcut_context(self) -> Any:
        """Auth-shortcut context for the Microsoft / xCloud launcher."""
        return _build_and_log("microsoft")

    async def get_ubisoft_auth_shortcut_context(self) -> Any:
        """Auth-shortcut context for the Ubisoft Connect launcher.

        Delegates to ``UbisoftStore.get_auth_shortcut_context``
        which has its own VDF-scan + repair logic. Falls back to
        a structured error if the Ubisoft store isn't registered
        (test installs, partial deployments).
        """
        logger.info("[AuthShortcuts:ubisoft] context requested")
        store = self.registry.get("ubisoft")
        if store is None:
            logger.warning(
                "[AuthShortcuts:ubisoft] store not registered",
            )
            return {"success": False, "error": "store_not_found"}
        if not hasattr(store, "get_auth_shortcut_context"):
            logger.warning(
                "[AuthShortcuts:ubisoft] store lacks "
                "get_auth_shortcut_context method",
            )
            return {"success": False, "error": "auth_shortcut_not_supported"}
        result = await store.get_auth_shortcut_context()
        logger.info(
            "[AuthShortcuts:ubisoft] context resolved: success=%s "
            "appid=%s",
            result.get("success"),
            result.get("appid_unsigned"),
        )
        return result

    async def get_compat_tool_for_game(self, store_game_id: str) -> Any:  # noqa: D401 — see below
        """See full doc on the body — logging-wrapped variant."""
        logger.info(
            "[AuthShortcuts] get_compat_tool_for_game(%s)",
            store_game_id,
        )
        result = await self._get_compat_tool_impl(store_game_id)
        logger.info(
            "[AuthShortcuts] compat_tool result: success=%s",
            result.get("success") if isinstance(result, dict) else "?",
        )
        return result

    async def _get_compat_tool_impl(self, store_game_id: str) -> Any:
        """Look up the current Proton compat tool for a shortcut.

        Frontend launchers call this to save/restore the
        compat tool around the auth flow. Delegates to the
        existing ``compatibility.proton_helpers`` helper.

        Args:
            store_game_id: the shortcut's ``store:game_id``
                key as written into LaunchOptions.

        Returns:
            ``{success, tool_name, current_launch_options,
              store_game_id, launcher_path}`` matching the
            ``ShortcutLaunchContext`` shape the frontend
            consumes.
        """
        try:
            from unifideck.compatibility.proton_helpers import (
                get_compat_tool_for_game as _lookup,
            )
            result = _lookup(store_game_id)
            if isinstance(result, dict):
                result.setdefault("success", True)
                return result
            return {"success": True, "tool_name": result or ""}
        except Exception as e:
            logger.warning(
                "[AuthShortcutsRPCMixin] "
                "get_compat_tool_for_game(%s) failed: %s",
                store_game_id, e,
            )
            return {"success": False, "error": str(e)}


# ─── Module-level helpers ─────────────────────────────────────


def _build_and_log(store: str) -> dict[str, Any]:
    """Compute + log the auth-shortcut context for ``store``.

    Wraps :func:`_build_auth_shortcut_context` with INFO-level
    log lines on entry and exit so the user can correlate a
    frontend connect click with a backend response without
    attaching a debugger.
    """
    logger.info("[AuthShortcuts:%s] context requested", store)
    result = _build_auth_shortcut_context(store)
    if result.get("success"):
        logger.info(
            "[AuthShortcuts:%s] context resolved: appid=%s "
            "launcher=%s",
            store,
            result.get("appid_unsigned"),
            result.get("launcher_path"),
        )
    else:
        logger.warning(
            "[AuthShortcuts:%s] context failed: %s",
            store,
            result.get("error"),
        )
    return result


def _build_auth_shortcut_context(store: str) -> dict[str, Any]:
    """Compute the auth-shortcut context for a CLI-driven store.

    Mirrors what the per-store ``_ensure_auth_shortcut`` method
    passes to ``ShortcutService.add_auth_shortcut`` so the
    appid we return matches what the backend actually wrote to
    ``shortcuts.vdf``. Returns the ``bin/unifideck-launcher``
    wrapper as the launcher_path so the frontend's
    temporary-shortcut fallback uses the actual executable
    (``dispatcher.py`` lacks the +x bit on purpose — it's
    imported, not run).
    """
    meta = _AUTH_SHORTCUT_META.get(store)
    if meta is None:
        return {"success": False, "error": "unknown_store"}
    plugin_dir = os.environ.get(
        "DECKY_PLUGIN_DIR",
        "/home/deck/homebrew/plugins/Unifideck",
    )
    dispatcher_path = os.path.join(
        plugin_dir, "py_modules", "unifideck", "launcher",
        "dispatcher.py",
    )
    wrapper_path = os.path.join(
        plugin_dir, "bin", "unifideck-launcher",
    )
    try:
        from unifideck.services.shortcut.games_map import (
            generate_app_id,
        )
        app_id = generate_app_id(dispatcher_path, meta["title"])
    except Exception as e:
        logger.warning(
            "[AuthShortcutsRPCMixin] generate_app_id failed "
            "for %s: %s",
            store, e,
        )
        return {"success": False, "error": "appid_failed"}
    unsigned = app_id if app_id >= 0 else app_id + 2**32
    return {
        "success": True,
        "appid_unsigned": unsigned,
        "launcher_path": wrapper_path,
        "launch_options": (
            f"{store}:{store}-auth {meta['env']}=auth"
        ),
        "launch_wait_ms": _AUTH_SHORTCUT_LAUNCH_WAIT_MS,
    }
