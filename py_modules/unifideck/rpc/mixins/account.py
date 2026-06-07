"""AccountRPCMixin — Steam account-switch modal RPCs.

Restores ``check_account_switch`` / ``migrate_account_data``, which
existed on the ``staging`` monolith (``main.py``) but were dropped in
the mixin refactor — so the frontend's ``AccountSwitchModal``
(driven from ``bootstrap-tasks.tsx``) never appeared.

Detection + artwork migration live in :class:`AccountManager`
(``unifideck.accounts``). Shortcut recreation for the new user is
driven here via the async ``ShortcutService.reconcile`` (the new
architecture's replacement for staging's synchronous
``reconcile_shortcuts_from_games_map``), fed the full library from
the sync layer.

The manager is cached on the host plugin instance so the
``previous_user_id`` resolved during ``check_account_switch`` survives
into the subsequent ``migrate_account_data`` call (the artwork copy
needs it).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AccountRPCMixin:
    """Account-switch detection + migration RPCs for the modal flow."""

    services: Any
    sync_service: Any

    def _account_manager(self) -> Any:
        """Return the cached :class:`AccountManager`, creating it once.

        Cached on the host plugin (``self``) rather than re-created per
        call so the ``previous_user_id`` from ``check_account_switch``
        is still set when ``migrate_account_data`` runs.
        """
        mgr = getattr(self, "_account_manager_inst", None)
        if mgr is None:
            from unifideck.accounts import AccountManager

            mgr = AccountManager()
            self._account_manager_inst = mgr
        return mgr

    async def check_account_switch(self) -> Any:
        """Detect a Steam account switch and report whether to prompt.

        Returns the shape ``AccountSwitchModal`` expects:
        ``{show_modal, current_user, previous_user, has_auth_tokens,
        has_registry}``. Records the current user afterwards so the
        modal shows once per switch, not on every launch.
        """
        mgr = self._account_manager()
        mgr.detect_account_switch()
        result = {
            "show_modal": mgr.should_show_modal(),
            "current_user": mgr.current_user_id,
            "previous_user": mgr.previous_user_id,
            "has_auth_tokens": mgr.has_active_auth_tokens(),
            "has_registry": mgr.has_registry_entries(),
        }
        mgr.save_current_user()
        return result

    async def migrate_account_data(self) -> Any:
        """Recreate the new user's shortcuts + copy artwork from the old.

        Shortcuts: re-run ``ShortcutService.reconcile`` over the full
        library so the active user's ``shortcuts.vdf`` is rebuilt with
        preserved appids (artwork/playtime survive). Artwork: copy the
        previous user's ``grid/`` folder. Returns
        ``{shortcuts_created, artwork_copied, errors}``.
        """
        mgr = self._account_manager()
        errors: list[str] = []

        shortcuts_created = 0
        shortcut_svc = getattr(self.services, "shortcut", None)
        if shortcut_svc is not None and self.sync_service is not None:
            try:
                games = self.sync_service.get_all_games()
                res = await shortcut_svc.reconcile(games)
                shortcuts_created = res.get("added", 0) + res.get("reclaimed", 0)
            except Exception as e:
                logger.exception("[AccountSwitch] shortcut reconcile failed")
                errors.append(f"shortcuts: {e}")
        else:
            errors.append("shortcut service or sync service unavailable")

        artwork = mgr.migrate_artwork()
        errors.extend(artwork.get("errors", []))

        mgr.account_switch_detected = False
        return {
            "shortcuts_created": shortcuts_created,
            "artwork_copied": artwork.get("copied", 0),
            "errors": errors,
        }
