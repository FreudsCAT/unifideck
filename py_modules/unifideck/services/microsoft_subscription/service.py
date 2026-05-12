"""Microsoft subscription service orchestration.

OP-22a | py_modules/unifideck/services/microsoft_subscription/service.py

``MicrosoftSubscriptionService`` composes :

* ``_CacheMixin``         — cached subscription state with TTL;
* ``_ProbeEmissionMixin`` — emit "subscription expired" / "renewed"
  probes when state transitions are detected;
* ``_EventHandlersMixin`` — react to relevant bus events (login,
  logout, manual refresh).

Public API : ``current_subscription()``, ``has_game_pass()``,
``refresh_now()``. State is conservative on uncertainty — when a
probe fails and no cache is available, we report "unknown" rather
than guessing.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING
from ...core.types import SubscriptionTier
from ...core.types.events import Events
from ...event_bus.event_bus import EventBus
from ...event_bus.event_bus_devex import auto_wire
from .cache_mixin import _CacheMixin
from .constants import _CACHE_STORE_NAME
from .event_handlers import _EventHandlersMixin
from .probe_emission import _ProbeEmissionMixin
from .time_utils import _fmt_ts

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...stores.microsoft.tokens import (
        MicrosoftTokenManager,
        XBLTokenChain,
    )
logger = logging.getLogger(__name__)


class MicrosoftSubscriptionService(
    _CacheMixin,
    _ProbeEmissionMixin,
    _EventHandlersMixin,
):
    """Microsoft subscription service."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cache = cache
        self._config = config
        try:
            self._cache.register(_CACHE_STORE_NAME, ttl_seconds=0)
        except Exception:
            logger.exception(
                "[MSSubSvc] could not register cache store %s",
                _CACHE_STORE_NAME,
            )
        self._lock = asyncio.Lock()
        self._last_emitted: dict[str, SubscriptionTier] = {}
        self._last_standard_chain: XBLTokenChain | None = None
        auto_wire(self, self._bus)
        logger.info(
            "[MSSubSvc] initialized (endpoint=%s)",
            self._probe_url(),
        )

    async def get_tier(self, token_manager: MicrosoftTokenManager) -> SubscriptionTier:
        """Get tier."""
        cache_key = await self._resolve_cache_key(token_manager)
        async with self._lock:
            cached = self._read_cache(cache_key)
            if cached is not None and cached.is_fresh():
                logger.debug(
                    "[MSSubSvc] cache hit for %s: tier=%s (expires in %ds)",
                    cache_key,
                    cached.tier.value,
                    int(cached.expires_at - time.time()),
                )
                return cached.tier
            probe_result = await self._run_probe(token_manager)
            if probe_result.ok:
                await self._store_tier_result(
                    cache_key,
                    probe_result.tier,
                )
                result_tier: SubscriptionTier = probe_result.tier
                return result_tier
            if cached is not None:
                logger.warning(
                    "[MSSubSvc] probe failed (%s), using stale cache tier=%s from %s",
                    probe_result.error,
                    cached.tier.value,
                    _fmt_ts(cached.detected_at),
                )
                return cached.tier
            await self._bus.emit(
                Events.SUBSCRIPTION_CHECK_FAILED,
                store="microsoft",
                reason=probe_result.error or "unknown",
            )
            logger.warning(
                "[MSSubSvc] probe failed (%s) and no cache — returning NONE",
                probe_result.error,
            )
            return SubscriptionTier.NONE

    async def has_active_subscription(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> bool:
        """Check whether active subscription."""
        tier = await self.get_tier(token_manager)
        return tier != SubscriptionTier.NONE

    async def invalidate(self) -> None:
        """Invalidate."""
        try:
            self._cache.clear(_CACHE_STORE_NAME)
        except Exception:
            logger.exception("[MSSubSvc] cache clear failed")
        self._last_emitted.clear()
        logger.info("[MSSubSvc] cache invalidated")
