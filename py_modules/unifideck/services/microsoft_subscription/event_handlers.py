"""Subscription event handlers — react to bus events.

OP-22d | py_modules/unifideck/services/microsoft_subscription/event_handlers.py

``_EventHandlersMixin`` subscribes to events that should trigger a
subscription refresh : Microsoft login success, manual refresh
button press, periodic timer tick. Decouples the trigger logic from
the service's public API.
"""

from __future__ import annotations
import logging
from typing import Any
from ...core.types import Events
from ...event_bus.event_bus_devex import subscribe

logger = logging.getLogger(__name__)


class _EventHandlersMixin:
    """Bus subscriptions glued onto ``MicrosoftSubscriptionService``."""

    @subscribe(Events.STORE_LOGOUT)
    async def _on_logout(self, **kwargs: Any) -> None:
        """Invalidate the cache when the user signs out of Microsoft.

        Filters by store: a logout event from a different store
        (e.g. Epic) is irrelevant — the user's Microsoft tier
        hasn't changed.
        """
        if kwargs.get("store") != "microsoft":
            return
        await self.invalidate()

    @subscribe(Events.STORE_AUTH_COMPLETE)
    async def _on_auth_complete(self, **kwargs: Any) -> None:
        """Invalidate the cache after a fresh Microsoft sign-in.

        The newly-authed user may be a different Xbox Live user
        than the cached entry — invalidating forces the next
        ``get_tier`` to probe with the new chain and produce a
        fresh entry under the right XUID key.
        """
        if kwargs.get("store") != "microsoft":
            return
        await self.invalidate()

    @subscribe(Events.ACCOUNT_SWITCHED)
    async def _on_account_switched(self, **kwargs: Any) -> None:
        """Invalidate the cache on a Steam account switch.

        Although the Microsoft-side state hasn't necessarily
        changed, a Steam account switch may indicate the device
        is being shared between users — playing it safe and
        forcing a re-probe is cheaper than silently serving a
        wrong tier.
        """
        await self.invalidate()
