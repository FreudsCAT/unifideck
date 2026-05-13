"""Probe emission mixin — emit probe events on state transitions.

OP-22c | py_modules/unifideck/services/microsoft_subscription/probe_emission.py

When the subscription state transitions (active → expired, none →
active, tier upgrade), ``_ProbeEmissionMixin`` emits a probe event
on the bus. Other services subscribe to these probes via
``ProbeReactionService`` (OP-12e).

Transitions are debounced — a brief flap (network glitch causing
"active → unknown → active" within seconds) doesn't emit two
spurious probes.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from .constants import _DEFAULT_PROBE_URL

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.types import SubscriptionTier
    from ...event_bus.event_bus import EventBus
    from ...stores.microsoft.microsoft_subscription import SubscriptionProbeResult
    from ...stores.microsoft.tokens import (
        MicrosoftTokenManager,
        XBLTokenChain,
    )
logger = logging.getLogger(__name__)


class _ProbeEmissionMixin:
    """Run XBL probes and emit state-change events."""

    _bus: EventBus
    _config: ConfigManager | None
    _last_emitted: dict[str, SubscriptionTier]
    _last_standard_chain: XBLTokenChain | None

    async def _run_probe(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> SubscriptionProbeResult:
        """Build a GSSV token chain and hit the subscription endpoint.

        The subscription probe requires a GSSV-flavoured XSTS
        token (different relying party from the standard XBL
        token). We re-use the standard XBL token from the cached
        chain if present (saves one round-trip), otherwise the
        chain build refreshes everything.

        A failed chain build is wrapped in a structured
        ``SubscriptionProbeResult`` with ``ok=False`` and a
        ``"gssv_chain_failed"`` error code, so the caller doesn't
        need to handle an exception path.

        Args:
            token_manager: the Microsoft token manager.

        Returns:
            ``SubscriptionProbeResult`` with either a resolved
            tier (``ok=True``) or an error code (``ok=False``).
        """
        from ...core.types import SubscriptionTier
        from ...stores.microsoft.microsoft_subscription import (
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
        """Return the subscription-probe endpoint URL.

        Reads ``stores.microsoft.subscription_check_url`` from the
        config (overridable for testing against a staging
        endpoint) and falls back to ``_DEFAULT_PROBE_URL``.

        Returns:
            The probe URL string.
        """
        if self._config is None:
            return _DEFAULT_PROBE_URL
        try:
            raw = self._config.get(
                "stores.microsoft.subscription_check_url",
            )
            return str(raw) if raw else _DEFAULT_PROBE_URL
        except Exception:
            return _DEFAULT_PROBE_URL

    async def _emit_state_change(self, cache_key: str, tier: SubscriptionTier) -> None:
        """Emit a state-change event iff the tier has actually changed.

        Per-user de-duplication via ``self._last_emitted`` —
        consecutive probes returning the same tier don't emit
        spurious events. The first probe for a user always emits
        (no prior value to compare against).

        Two events:

        * ``SUBSCRIPTION_EXPIRED`` when transitioning to ``NONE``;
        * ``SUBSCRIPTION_DETECTED`` for any non-``NONE`` tier
          (with the tier value in the payload for consumers that
          need to discriminate Ultimate / PC Pass / etc.).

        Args:
            cache_key: cache key (de-dup key).
            tier: the new tier.
        """
        from ...core.types import Events, SubscriptionTier

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
