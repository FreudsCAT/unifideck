from __future__ import annotations

import logging
from typing import Any

from unifideck.core.types import Events
from unifideck.event_bus.event_bus_devex import subscribe

logger = logging.getLogger(__name__)
class _EventHandlersMixin:
    """Event handlers mixin."""
    @subscribe(Events.STORE_LOGOUT)
    async def _on_logout(self, **kwargs: Any) -> None:
        """On logout."""
        if kwargs.get("store") != "microsoft":
            return
        await self.invalidate()  # type: ignore[attr-defined]  # self.invalidate provided by sibling mixin _CacheMixin
    @subscribe(Events.STORE_AUTH_COMPLETE)
    async def _on_auth_complete(self, **kwargs: Any) -> None:
        """On auth complete."""
        if kwargs.get("store") != "microsoft":
            return
        await self.invalidate()  # type: ignore[attr-defined]  # self.invalidate provided by sibling mixin _CacheMixin
    @subscribe(Events.ACCOUNT_SWITCHED)
    async def _on_account_switched(self, **kwargs: Any) -> None:
        """On account switched."""
        await self.invalidate()  # type: ignore[attr-defined]  # self.invalidate provided by sibling mixin _CacheMixin
