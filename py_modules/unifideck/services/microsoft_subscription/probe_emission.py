from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .constants import _DEFAULT_PROBE_URL

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.types import SubscriptionTier
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.stores.microsoft.microsoft_subscription import (
        SubscriptionProbeResult,
    )
    from unifideck.stores.microsoft.tokens import MicrosoftTokenManager, XBLTokenChain
logger = logging.getLogger(__name__)
class _ProbeEmissionMixin:
    """Probe emission mixin."""
    _bus: EventBus
    _config: ConfigManager | None
    _last_emitted: dict[str, SubscriptionTier]
    _last_standard_chain: XBLTokenChain | None
    async def _run_probe(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> SubscriptionProbeResult:
        """Run probe."""
        from unifideck.core.types import SubscriptionTier
        from unifideck.stores.microsoft.microsoft_subscription import (
            SubscriptionProbeResult,
            probe_subscription,
        )
        xbl_token = None
        if self._last_standard_chain is not None:
            xbl_token = self._last_standard_chain.xbl_token
        gssv_chain = await token_manager.build_gssv_chain(
            xbl_token=xbl_token,
        )
        if gssv_chain is None:
            return SubscriptionProbeResult(
                tier=SubscriptionTier.NONE,
                ok=False,
                error="gssv_chain_failed",
            )
        return await probe_subscription(
            user_hash=gssv_chain.user_hash,
            gssv_xsts_token=gssv_chain.xsts_token,
            endpoint_url=self._probe_url(),
        )
    def _probe_url(self) -> str:
        """Probe URL."""
        if self._config is None:
            return _DEFAULT_PROBE_URL
        try:
            raw = self._config.get(
                "stores.microsoft.subscription_check_url",
            )
            return str(raw) if raw else _DEFAULT_PROBE_URL
        except Exception:
            return _DEFAULT_PROBE_URL

    async def _emit_state_change(
        self,
        cache_key: str,
        tier: SubscriptionTier,
    ) -> None:

        """Emit state change."""
        from unifideck.core.types import Events, SubscriptionTier
        last = self._last_emitted.get(cache_key)
        if last == tier:
            return
        self._last_emitted[cache_key] = tier
        if tier == SubscriptionTier.NONE:
            await self._bus.emit(
                Events.SUBSCRIPTION_EXPIRED,
                store="microsoft",
            )
        else:
            await self._bus.emit(
                Events.SUBSCRIPTION_DETECTED,
                store="microsoft",
                tier=tier.value,
            )
