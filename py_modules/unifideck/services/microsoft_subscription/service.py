from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from unifideck.core.types import SubscriptionTier
from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus import EventBus
from unifideck.event_bus.event_bus_devex import auto_wire

from .cache_mixin import _CacheMixin
from .constants import _CACHE_STORE_NAME
from .event_handlers import _EventHandlersMixin
from .probe_emission import _ProbeEmissionMixin
from .time_utils import _fmt_ts

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.stores.microsoft.microsoft_subscription import (
        SubscriptionProbeResult,
    )
    from unifideck.stores.microsoft.tokens import MicrosoftTokenManager, XBLTokenChain
logger = logging.getLogger(__name__)

# gsToken JWTs are issued with ~4h expiry. Reuse a probe result within
# this window to avoid hitting the login service every sync.
_PROBE_SESSION_TTL_SECONDS = 60 * 50
class MicrosoftSubscriptionService(
    _CacheMixin, _ProbeEmissionMixin, _EventHandlersMixin,
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
        # In-memory probe session: holds the most recent SubscriptionProbeResult
        # (gsToken + regions + market) so downstream consumers (catalog reader)
        # can reuse it within the JWT's lifetime without re-probing.
        self._last_probe: SubscriptionProbeResult | None = None
        self._last_probe_at: float = 0.0
        auto_wire(self, self._bus)
        logger.info(
            "[MSSubSvc] initialized (endpoint=%s)",
            self._probe_url(),
        )

    async def get_tier(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> SubscriptionTier:

        """Get tier."""
        cache_key = await self._resolve_cache_key(token_manager)
        async with self._lock:
            cached = self._read_cache(cache_key)
            if cached is not None and cached.is_fresh():
                logger.debug(
                    "[MSSubSvc] cache hit for %s: tier=%s "
                    "(expires in %ds)",
                    cache_key,
                    cached.tier.value,
                    int(cached.expires_at - time.time()),
                )
                return cached.tier
            probe_result = await self._run_probe(token_manager)
            if probe_result.ok:
                # Persist session artefacts (gsToken/regions/market) for
                # the catalog reader to reuse within the JWT lifetime.
                self._last_probe = probe_result
                self._last_probe_at = time.time()
                await self._store_tier_result(
                    cache_key, probe_result.tier,
                )
                result_tier: SubscriptionTier = probe_result.tier
                return result_tier
            if cached is not None:
                logger.warning(
                    "[MSSubSvc] probe failed (%s), using stale "
                    "cache tier=%s from %s",
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
                "[MSSubSvc] probe failed (%s) and no cache "
                "— returning NONE",
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

    async def get_session(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> SubscriptionProbeResult | None:
        """Return a fresh-enough probe result for downstream use.

        The xCloud catalog reader needs the ``gsToken`` and ``regions``
        from the login response to call the regional ``/v2/titles``
        endpoint. We reuse the last probe result while still valid,
        otherwise re-probe and return that.

        Returns None if no successful probe is available (e.g.
        subscription gate has rejected the account).
        """
        now = time.time()
        if (
            self._last_probe is not None
            and self._last_probe.gs_token
            and now - self._last_probe_at < _PROBE_SESSION_TTL_SECONDS
        ):
            return self._last_probe
        # Force a fresh probe — get_tier handles capture into _last_probe
        await self.get_tier(token_manager)
        if (
            self._last_probe is not None
            and self._last_probe.gs_token
        ):
            return self._last_probe
        return None
    async def invalidate(self) -> None:
        """Invalidate."""
        try:
            self._cache.clear(_CACHE_STORE_NAME)
        except Exception:
            logger.exception("[MSSubSvc] cache clear failed")
        self._last_emitted.clear()
        logger.info("[MSSubSvc] cache invalidated")
