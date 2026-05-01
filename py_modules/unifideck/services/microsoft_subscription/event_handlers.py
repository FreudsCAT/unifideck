"""services/microsoft_subscription/event_handlers.py"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from .service import MicrosoftSubscriptionService

logger = logging.getLogger(__name__)


class _EventHandlersMixin:
    # Requires self.invalidate() from host class
    
    @subscribe(Events.STORE_LOGOUT)
    async def _on_logout(self: Any, **kwargs: Any) -> None:
        if kwargs.get("store") != "microsoft":
            return
        await self.invalidate()

    @subscribe(Events.STORE_AUTH_COMPLETE)
    async def _on_auth_complete(self: Any, **kwargs: Any) -> None:
        if kwargs.get("store") != "microsoft":
            return
        await self.invalidate()

    @subscribe(Events.ACCOUNT_SWITCHED)
    async def _on_account_switched(self: Any, **kwargs: Any) -> None:
        await self.invalidate()
