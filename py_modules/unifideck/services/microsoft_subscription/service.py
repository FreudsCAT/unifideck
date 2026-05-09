"""services/microsoft_subscription/service.py — xCloud subscription state.

Layer-5 service that owns Microsoft Xbox Game Pass subscription
detection. Reactive to auth lifecycle (STORE_LOGOUT,
STORE_AUTH_COMPLETE, ACCOUNT_SWITCHED). MicrosoftStore queries
``get_tier(token_manager)`` before every library sync.

Shell class composed of 3 mixins:
- ``_CacheMixin``        : read/write/store cached entries
- ``_ProbeEmissionMixin`` : HTTP probe + EventBus emission
- ``_EventHandlersMixin`` : auth lifecycle subscribers
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...core.types import SubscriptionTier
from .cache_mixin import _CacheMixin
from .constants import _CACHE_STORE_NAME
from .event_handlers import _EventHandlersMixin
from .probe_emission import _ProbeEmissionMixin

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.event_bus import EventBus
    from ...stores.microsoft.tokens import MicrosoftTokenManager

logger = logging.getLogger(__name__)


class MicrosoftSubscriptionService(
    _CacheMixin, _ProbeEmissionMixin, _EventHandlersMixin,
):
    """Reactive xCloud subscription state for MicrosoftStore."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        config: ConfigManager | None = None,
    ) -> None:
        """Store collaborators, init cache state and locks."""
        self._bus = bus
        self._cache = cache
        self._config = config
        
        self._last_emitted: dict[str, SubscriptionTier] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_standard_chain = None
        
        # Load existing cache to populate _last_emitted
        try:
            store_data = self._cache.get_store(_CACHE_STORE_NAME)
            if store_data:
                for key, raw in store_data.items():
                    entry = self._read_cache(key)
                    if entry and entry.is_fresh():
                        self._last_emitted[key] = entry.tier
        except Exception as e:
            logger.debug("[MicrosoftSubscription] Failed to load initial cache: %s", e)
            
        if hasattr(self._bus, "auto_wire"):
            self._bus.auto_wire(self)

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_tier(
        self, token_manager: MicrosoftTokenManager,
    ) -> SubscriptionTier:
        """Return the currently detected subscription tier."""
        cache_key = await self._resolve_cache_key(token_manager)
        lock = self._get_lock(cache_key)
        
        async with lock:
            # Check cache
            cached = self._read_cache(cache_key)
            if cached and cached.is_fresh():
                return cached.tier
                
            # Cache miss or expired — probe
            logger.info("[MicrosoftSubscription] Probing subscription for %s", cache_key)
            result = await self._run_probe(token_manager)
            
            if result.ok:
                await self._store_tier_result(cache_key, result.tier)
                return result.tier
            else:
                logger.warning(
                    "[MicrosoftSubscription] Probe failed for %s: %s", 
                    cache_key, result.error
                )
                return SubscriptionTier.NONE

    async def has_active_subscription(
        self, token_manager: MicrosoftTokenManager,
    ) -> bool:
        """Convenience wrapper for get_tier() != NONE."""
        tier = await self.get_tier(token_manager)
        return tier != SubscriptionTier.NONE

    async def invalidate(self) -> None:
        """Drop every cached entry (explicit refresh)."""
        logger.info("[MicrosoftSubscription] Invalidating subscription cache")
        self._last_emitted.clear()
        self._last_standard_chain = None
        
        try:
            self._cache.clear_store(_CACHE_STORE_NAME)
        except Exception as e:
            logger.warning("[MicrosoftSubscription] Failed to clear cache store: %s", e)
