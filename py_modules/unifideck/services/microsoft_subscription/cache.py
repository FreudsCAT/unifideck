"""Subscription cache entry — typed dataclass.

OP-22e | py_modules/unifideck/services/microsoft_subscription/cache.py

``_CachedEntry`` is the frozen dataclass for a cached subscription
state : tier, validity (active / expired / unknown), check timestamp,
optional end-of-period timestamp. Serialisable to/from JSON for the
on-disk cache.
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any
from ...core.types import SubscriptionTier


@dataclass(frozen=True)
class _CachedEntry:
    """Cached entry."""

    tier: SubscriptionTier
    expires_at: float
    detected_at: float

    def is_fresh(self, now: float | None = None) -> bool:
        """Check whether fresh."""
        return (now if now is not None else time.time()) < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return {
            "tier": self.tier.value,
            "expires_at": self.expires_at,
            "detected_at": self.detected_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _CachedEntry | None:
        """From dict."""
        try:
            return cls(
                tier=SubscriptionTier(raw["tier"]),
                expires_at=float(raw["expires_at"]),
                detected_at=float(raw["detected_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None
