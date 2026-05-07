"""services/microsoft_subscription/probe_emission.py — I/O group mixin.

Fuses outbound HTTP probe and EventBus emission — both talk to
the outside world (network + bus). Grouped so the service's
outbound surface lives in one place.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .constants import _DEFAULT_PROBE_URL

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.types import SubscriptionTier
    from ...event_bus.event_bus import EventBus
    from ...stores.microsoft.microsoft_subscription import SubscriptionProbeResult
    from ...stores.microsoft.tokens import MicrosoftTokenManager, XBLTokenChain

logger = logging.getLogger(__name__)


class _ProbeEmissionMixin:
    """Outbound I/O — network probe + event bus emit."""
    
    _bus: EventBus
    _config: ConfigManager | None
    _last_emitted: dict[str, SubscriptionTier]
    _last_standard_chain: XBLTokenChain | None

    async def _run_probe(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> SubscriptionProbeResult:
        """Obtain GSSV XSTS and call ``probe_subscription``."""
        from ...core.types import SubscriptionTier
        from ...stores.microsoft.microsoft_subscription import (
            SubscriptionProbeResult,
            probe_subscription,
        )
        
        try:
            # Reuses the XBL token from the last standard chain if possible
            xbl_token = None
            if self._last_standard_chain:
                xbl_token = self._last_standard_chain.xbl_token
                
            # Request GSSV chain for subscription check
            chain = await token_manager.get_gssv_chain(xbl_token=xbl_token)
            if not chain:
                return SubscriptionProbeResult(
                    tier=SubscriptionTier.NONE, 
                    ok=False, 
                    error="gssv_chain_failed"
                )
                
            url = self._probe_url()
            return await probe_subscription(chain.xsts_token, url=url)
            
        except Exception as e:
            logger.warning("[MicrosoftSubscription] Probe failed: %s", e)
            return SubscriptionProbeResult(
                tier=SubscriptionTier.NONE, 
                ok=False, 
                error=str(e)
            )

    def _probe_url(self) -> str:
        """Return the configured probe endpoint URL."""
        if not self._config:
            return _DEFAULT_PROBE_URL
        return self._config.get("microsoft.subscription_probe_url", _DEFAULT_PROBE_URL)

    async def _emit_state_change(
        self,
        cache_key: str,
        tier: SubscriptionTier,
    ) -> None:
        """Emit ``SUBSCRIPTION_DETECTED`` / ``_EXPIRED`` on transitions."""
        from ...core.types import SubscriptionTier
        from ...core.types.events import Events
        
        last_tier = self._last_emitted.get(cache_key)
        if last_tier == tier:
            return  # No state change
            
        self._last_emitted[cache_key] = tier
        
        try:
            if tier == SubscriptionTier.NONE:
                self._bus.emit(Events.SUBSCRIPTION_EXPIRED, store="microsoft", cache_key=cache_key)
            else:
                self._bus.emit(
                    Events.SUBSCRIPTION_DETECTED, 
                    store="microsoft", 
                    cache_key=cache_key, 
                    tier=tier.value
                )
        except Exception as e:
            logger.warning("[MicrosoftSubscription] Failed to emit state change: %s", e)
