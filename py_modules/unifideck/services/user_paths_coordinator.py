"""services/user_paths_coordinator.py — re-bind per-user paths on account change.

``ServicePaths`` resolves each per-user path (``shortcuts.vdf``, ``grid/``,
per-user ``localconfig.vdf``) exactly once at boot. But the authoritative
active user can change *after* boot — the frontend pushes the live logged-in
user (``set_active_steam_user`` RPC), or the user switches Steam accounts
(``AccountService`` emits ``ACCOUNT_SWITCHED``). Historically nothing re-bound
those paths, so the plugin kept writing to the boot-time (possibly wrong) user.

This coordinator is the single reactor: on ``ACCOUNT_SWITCHED`` it re-resolves
the new user's per-user paths and pushes them onto ShortcutService /
ArtworkService / ProtonService via
:func:`unifideck.steam.current_user.rebind_user_paths`. The ``set_active_steam_user``
RPC calls the same helper directly, so both funnel through identical logic.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe
from unifideck.steam.current_user import rebind_user_paths

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.services.bootstrap.container import ServiceContainer

logger = logging.getLogger(__name__)


class UserPathsCoordinator:
    """Re-binds per-user service paths when the active Steam user changes."""

    def __init__(
        self, bus: EventBus, container: ServiceContainer, steam_root: str,
    ) -> None:
        """Store refs and wire the ``ACCOUNT_SWITCHED`` handler."""
        self._bus = bus
        self._container = container
        self._steam_root = steam_root
        auto_wire(self, self._bus)

    @subscribe(Events.ACCOUNT_SWITCHED)
    async def _on_account_switched(self, **kwargs: Any) -> None:
        """Re-bind every per-user service path to the new account.

        The emitter's payload key has historically been ``new_user``; accept
        ``active_user_id`` too so this keeps working once that contract is
        aligned. A missing/empty id is ignored (nothing to re-bind to).
        """
        account_id = kwargs.get("active_user_id") or kwargs.get("new_user")
        if not account_id:
            return
        logger.info(
            "[UserPathsCoordinator] account switched to %s — re-binding paths",
            account_id,
        )
        rebind_user_paths(self._container, Path(self._steam_root), str(account_id))

    def rebind(self, account_id: str) -> None:
        """Imperative re-bind (used by the frontend-push RPC)."""
        rebind_user_paths(self._container, Path(self._steam_root), account_id)


__all__ = ["UserPathsCoordinator"]
