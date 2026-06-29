"""services/playtime_sync — push local play sessions up to the stores.

Re-exports ``PlaytimeSyncService`` so the bootstrap wiring table can write
``from unifideck.services.playtime_sync import PlaytimeSyncService``.
"""
from __future__ import annotations

from .service import PlaytimeSyncService

__all__ = ["PlaytimeSyncService"]
