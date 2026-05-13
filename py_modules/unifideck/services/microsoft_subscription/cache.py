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
    """One persisted subscription-tier entry.

    Three fields fully describe a cached probe outcome:

    Attributes:
        tier: the resolved subscription tier.
        expires_at: POSIX timestamp at which this entry should be
            considered stale (typically the next end-of-month UTC
            boundary).
        detected_at: POSIX timestamp at which the probe ran.
            Useful for diagnostics: ``"last detected on …"``.
    """

    tier: SubscriptionTier
    expires_at: float
    detected_at: float

    def is_fresh(self, now: float | None = None) -> bool:
        """Return whether the entry is still within its validity window.

        Args:
            now: optional POSIX timestamp to compare against.
                Defaults to ``time.time()`` — overrideable for
                deterministic tests.

        Returns:
            ``True`` iff ``now < expires_at``.
        """
        return (now if now is not None else time.time()) < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict for the cache.

        Stores ``tier`` as its string value (not the enum object)
        so the cache backend doesn't need to know about
        ``SubscriptionTier``.

        Returns:
            Dict with ``tier`` (str), ``expires_at`` (float),
            ``detected_at`` (float).
        """
        return {
            "tier": self.tier.value,
            "expires_at": self.expires_at,
            "detected_at": self.detected_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _CachedEntry | None:
        """Reconstruct from a dict produced by ``to_dict``.

        Returns ``None`` on malformed input (missing key,
        non-numeric timestamps, unknown tier value). The caller
        treats ``None`` as a cache miss rather than as an error.

        Args:
            raw: dict typically from the cache backend.

        Returns:
            A fresh ``_CachedEntry``, or ``None`` if the dict
            doesn't match the expected schema.
        """
        try:
            return cls(
                tier=SubscriptionTier(raw["tier"]),
                expires_at=float(raw["expires_at"]),
                detected_at=float(raw["detected_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None
