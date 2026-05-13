"""Subscription cache mixin.

OP-22b | py_modules/unifideck/services/microsoft_subscription/cache_mixin.py

``_CacheMixin`` exposes the cached-state surface :

* ``load_cached`` — read from disk on boot;
* ``save_cached`` — flush after every refresh;
* TTL accessor — derive the next expiration from the cached
  entry's ``checked_at`` + the configured window.

The cache survives plugin restarts so the user doesn't see a
spinner on every boot — the cached state is shown immediately
while a background refresh runs.
"""

from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING
from .cache import _CachedEntry
from .constants import _CACHE_KEY_PREFIX, _CACHE_STORE_NAME
from .time_utils import _end_of_month_utc

if TYPE_CHECKING:
    from ...core.cache_manager import CacheManager
    from ...core.types import SubscriptionTier
    from ...stores.microsoft.tokens import (
        MicrosoftTokenManager,
        XBLTokenChain,
    )
logger = logging.getLogger(__name__)


class _CacheMixin:
    """Per-XUID cache layer for subscription tiers."""

    _cache: CacheManager
    _last_standard_chain: XBLTokenChain | None

    async def _resolve_cache_key(self, token_manager: MicrosoftTokenManager) -> str:
        """Compute the cache key for the current user.

        The key is ``<prefix><xuid>`` where ``xuid`` is the Xbox
        Live user id pulled from the user's XBL token chain. This
        guarantees that a Steam-account switch (or a sign-out +
        sign-in as a different user) automatically maps to a
        different cache key — we never display one user's tier
        for another user.

        If the chain build fails (no token, network error), the
        key falls back to ``<prefix>default``. The fallback is
        intentionally non-empty so a probe failure on first use
        still produces a cacheable result (the failure mode is
        better than thrashing on an empty key).

        Args:
            token_manager: the Microsoft token manager.

        Returns:
            The cache key string.
        """
        xuid: str | None = None
        try:
            chain = await token_manager.build_chain()
            if chain is not None:
                xuid = chain.xuid
                self._last_standard_chain = chain
        except Exception:
            logger.debug(
                "[MSSubSvc] could not build chain for key resolution",
                exc_info=True,
            )
        return f"{_CACHE_KEY_PREFIX}{xuid or 'default'}"

    def _read_cache(self, key: str) -> _CachedEntry | None:
        """Read a cached tier entry by key.

        Cache-layer failures (rare: corrupted entry, deserialiser
        error) are absorbed into ``None`` so the calling
        ``get_tier`` flow re-probes rather than crashing.

        Args:
            key: cache key from ``_resolve_cache_key``.

        Returns:
            The deserialised ``_CachedEntry``, or ``None`` if the
            key is absent or unreadable.
        """
        try:
            raw = self._cache.get(_CACHE_STORE_NAME, key)
        except Exception:
            logger.exception("[MSSubSvc] cache read failed")
            return None
        if raw is None:
            return None
        if isinstance(raw, dict):
            return _CachedEntry.from_dict(raw)
        return None

    def _write_cache(self, key: str, entry: _CachedEntry) -> None:
        """Persist a tier entry to the cache.

        Failures are logged but not raised — the in-memory result
        is what the caller actually returns. A failed cache write
        just means the next call will re-probe.

        Args:
            key: cache key from ``_resolve_cache_key``.
            entry: the entry to persist.
        """
        try:
            self._cache.set(_CACHE_STORE_NAME, key, entry.to_dict())
        except Exception:
            logger.exception("[MSSubSvc] cache write failed")

    async def _store_tier_result(self, cache_key: str, tier: SubscriptionTier) -> None:
        """Build a ``_CachedEntry`` and emit the state-change event.

        The expiry is set to the next end-of-month UTC boundary
        (Microsoft subscriptions renew monthly), so the cache
        naturally invalidates when the user's billing cycle rolls
        over.

        After the write, ``_emit_state_change`` (from
        ``_ProbeEmissionMixin``) emits a tier-change event if and
        only if the tier differs from the last-emitted one for
        this user.

        Args:
            cache_key: cache key from ``_resolve_cache_key``.
            tier: the freshly-probed subscription tier.
        """
        entry = _CachedEntry(
            tier=tier,
            expires_at=_end_of_month_utc(),
            detected_at=time.time(),
        )
        self._write_cache(cache_key, entry)
        await self._emit_state_change(cache_key, tier)
