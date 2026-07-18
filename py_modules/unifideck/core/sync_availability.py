"""core/sync_availability.py — fresh per-store availability before a sync.

Split out of ``sync_run_mixin.py`` (was pushing it over the volumetry
file cap). ``StoreRegistry.available()`` — what ``_setup_sync`` reads
right after calling this — just returns stores whose ``_cached_available``
flag is truthy. Nothing previously refreshed that flag on a successful
login (only an explicit status check, e.g. opening the Settings/Store
tab, or a fresh boot, did), so a sync run immediately after signing
into a store silently skipped it: the newly-authed store only "became
available" after the next restart forced a fresh check.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.stores import StoreRegistry
    from unifideck.stores.shared.store_base import StoreBase

logger = logging.getLogger(__name__)


async def refresh_store_availability(registry: StoreRegistry) -> None:
    """Fresh ``is_available()`` per registered store, run concurrently.

    Concurrent so one slow store (e.g. Microsoft's token refresh, a
    real network round-trip) doesn't serialize in front of the others;
    per-store failures are tolerated so one broken check can't skip
    the whole sync.
    """
    async def _check(store: StoreBase) -> None:
        try:
            store._cached_available = await store.is_available()
        except Exception:
            logger.warning(
                "[SyncService] availability refresh failed for %s",
                store.store_name,
            )

    await asyncio.gather(*(_check(s) for s in registry.all()))
