"""Compatibility post-sync service — ProtonDB + Deck Verified ratings.

Wires :class:`unifideck.compatibility.CompatLibrary` into the sync
pipeline. After SYNC_COMPLETE, walks the game list and resolves each
title to a ProtonDB tier + Deck-Verified status, caching the result
under the existing ``compat`` namespace.

Without this service, ``compatibility.CompatLibrary`` sits unused —
ProtonDB tiers never get pre-fetched, so game tiles show no badge
until the user manually opens game details (which triggers a live
lookup, blocking the UI for ~1s).
"""
from __future__ import annotations

from .service import CompatibilityService

__all__ = ["CompatibilityService"]
