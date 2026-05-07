"""services/microsoft_subscription/cache_mixin.py — Persistent cache ops.

Mixin providing read/write to the persisted subscription cache.
The ``_store_tier_result`` helper writes the cache then fires
the emission — the cross-mixin call that Option C accepts in
exchange for grouping I/O primitives.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .cache import _CachedEntry
from .constants import _CACHE_KEY_PREFIX, _CACHE_STORE_NAME
from .time_utils import _end_of_month_utc

if TYPE_CHECKING:
    from ...core.cache_manager import CacheManager
    from ...core.types import SubscriptionTier
    from ...stores.microsoft.tokens import MicrosoftTokenManager, XBLTokenChain

logger = logging.getLogger(__name__)


class _CacheMixin:
    """Cache read/write ops for MicrosoftSubscriptionService."""
    
    _cache: CacheManager
    _last_standard_chain: XBLTokenChain | None

    async def _resolve_cache_key(
        self,
        token_manager: MicrosoftTokenManager,
    ) -> str:
        """Build a cache key from the token manager's current account."""
        try:
            # Try to get the standard chain to extract xuid
            chain = await token_manager.get_standard_chain()
            self._last_standard_chain = chain
            
            if chain and chain.xuid:
                return f"{_CACHE_KEY_PREFIX}{chain.xuid}"
        except Exception as e:
            logger.debug("[MicrosoftSubscription] Failed to build standard chain for cache key: %s", e)
            
        return f"{_CACHE_KEY_PREFIX}default"

    def _read_cache(self, key: str) -> _CachedEntry | None:
        """Return the cached entry for ``key`` or None on miss/corrupt."""
        try:
            raw = self._cache.get(_CACHE_STORE_NAME, key)
            if not raw or not isinstance(raw, dict):
                return None
                
            return _CachedEntry.from_dict(raw)
        except Exception as e:
            logger.debug("[MicrosoftSubscription] Failed to read cache for %s: %s", key, e)
            return None

    def _write_cache(self, key: str, entry: _CachedEntry) -> None:
        """Persist a cache entry. Failures are logged, not raised."""
        try:
            self._cache.set(_CACHE_STORE_NAME, key, entry.to_dict())
        except Exception as e:
            logger.warning("[MicrosoftSubscription] Failed to write cache for %s: %s", key, e)

    async def _store_tier_result(
        self,
        cache_key: str,
        tier: SubscriptionTier,
    ) -> None:
        """Persist a fresh probe result and emit the state-change."""
        now = time.time()
        expiry = _end_of_month_utc()
        
        entry = _CachedEntry(
            tier=tier,
            expires_at=expiry,
            detected_at=now,
        )
        
        self._write_cache(cache_key, entry)
        
        if hasattr(self, "_emit_state_change"):
            await self._emit_state_change(cache_key, tier)
