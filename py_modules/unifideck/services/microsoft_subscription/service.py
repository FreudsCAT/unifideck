"""Microsoft subscription service — Game Pass / Core tier detection.

OP-22a | py_modules/unifideck/services/microsoft_subscription/service.py

``MicrosoftSubscriptionService`` is responsible for answering the
question "what Game Pass / Xbox Core tier does this user currently
have?". The answer feeds into ``stores.microsoft`` to decide which
games to display and which to gate behind a subscription.

Composes four concerns:

* ``_CacheMixin``         — cached subscription state with TTL
  scoped by user-id (so a Steam account switch doesn't show the
  previous user's tier);
* ``_ProbeEmissionMixin`` — actual XBL probe call;
* ``_EventHandlersMixin`` — subscribe to bus events that should
  trigger refresh (login, logout, account switch, manual refresh).

State is conservative on uncertainty — when the probe fails **and**
no cache is available, the service reports ``NONE`` and emits a
``SUBSCRIPTION_CHECK_FAILED`` event. The Microsoft store then
displays a banner rather than silently hiding all the user's
subscription games.
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
    """Detect, cache and expose the Xbox / Game Pass tier per user."""

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        config: ConfigManager | None = None,
    ) -> None:
        """Wire the service to its dependencies and register cache store.

        The cache store is registered with ``ttl_seconds=0`` —
        TTL is enforced per-entry by the ``is_fresh`` helper
        rather than by the cache manager itself, so different
        tiers can have different freshness windows in the future.

        ``_lock`` serializes calls to ``get_tier`` so two
        concurrent UI requests don't both hit the XBL probe
        endpoint (rate-limit avoidance).

        Args:
            bus: live event bus on which the service emits probe
                outcomes and subscribes to refresh-triggering
                events.
            cache: shared cache manager (registers a dedicated
                store for subscription tiers).
            config: optional config manager forwarded to the
                probe mixin for endpoint and timeout tunables.
        """
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
        """Return the user's current subscription tier.

        Decision flow (under the service lock):

        1. **Cache hit and fresh** → return cached tier
           immediately (no network call).
        2. **Cache miss or stale** → run the XBL probe:
           - probe succeeds → cache the result, return tier;
           - probe fails AND we have a stale cache → log warning,
             return the stale tier (degraded mode: better than
             nothing);
           - probe fails AND no cache → emit
             ``SUBSCRIPTION_CHECK_FAILED``, return ``NONE``.

        Args:
            token_manager: the Microsoft token manager used to
                obtain a fresh XBL token chain for the probe.

        Returns:
            One of ``SubscriptionTier`` (``ULTIMATE``,
            ``PC_PASS``, ``CONSOLE``, ``CORE``, ``NONE``).
        """
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
        """Convenience wrapper: any tier other than ``NONE`` counts as active.

        Args:
            token_manager: the Microsoft token manager.

        Returns:
            ``True`` iff the resolved tier is one of the paid
            tiers (``ULTIMATE`` / ``PC_PASS`` / ``CONSOLE`` /
            ``CORE``).
        """
        tier = await self.get_tier(token_manager)
        return tier != SubscriptionTier.NONE

    async def invalidate(self) -> None:
        """Clear every cached tier and force re-probe on next ``get_tier``.

        Called by ``_EventHandlersMixin`` on account-switch /
        logout events, and exposed publicly for RPC-triggered
        manual refreshes from the QAM panel.

        Also clears ``_last_emitted`` so the next probe will emit
        a tier-change event even if the new tier matches what
        was last seen (defensive: a manual invalidation means the
        user wants to know the current state explicitly).
        """
        try:
            self._cache.clear(_CACHE_STORE_NAME)
        except Exception:
            logger.exception("[MSSubSvc] cache clear failed")
        self._last_emitted.clear()
        logger.info("[MSSubSvc] cache invalidated")
