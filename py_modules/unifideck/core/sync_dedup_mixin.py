"""Dedup + result-aggregation mixin for :class:`SyncService`.

OP-08l-ter | py_modules/unifideck/core/sync_dedup_mixin.py

Extracted from ``core/sync_service.py`` (2026-05-17) to keep
the host file under the 550 LOC volumetry cap. Three
self-contained methods that read from the sync service's
collaborators (registry, bus, config) but don't mutate
sync-run state directly:

* ``_apply_dedup_and_emit`` — cross-store dedup + event.
* ``_tracked_stores``       — config-backed priority list.
* ``_aggregate_results``    — build the final ``SyncResult``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .types import Game, SyncResult

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.event_bus import EventBus
    from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)


class _SyncDedupMixin:
    """Dedup + result helpers for :class:`SyncService`."""

    # Attributes provided by the host SyncService at runtime.
    _registry: StoreRegistry
    _bus: EventBus
    _config: ConfigManager | None

    async def _apply_dedup_and_emit(
        self,
        libraries: dict[str, list[Game]],
    ) -> dict[str, list[Game]]:
        """Dedup disabled — returns libraries unchanged.

        Previously ran ``cross_store_dedup.deduplicate_libraries``
        (cross-store match + Steam-owned filter) and emitted
        ``SYNC_DEDUP``. Disabled per user request: duplicates
        across stores are now shown. Re-enable by restoring
        the original body from git history.
        """
        return libraries

    def _tracked_stores(self) -> tuple[str, ...]:
        """Resolve the tracked-stores list for dedup priority.

        Reads ``dedup.tracked_stores`` from config; falls back to
        the four-store hardcoded default on missing config,
        config error, or wrong type.
        """
        default = ("epic", "gog", "amazon", "ubisoft")
        if self._config is None:
            return default
        try:
            value = self._config.get("dedup.tracked_stores", list(default))
        except Exception:
            return default
        if not isinstance(value, (list, tuple)):
            logger.warning(
                "[SyncService] dedup.tracked_stores has wrong type "
                "(%s); falling back to defaults",
                type(value).__name__,
            )
            return default
        return tuple(value)

    def _aggregate_results(
        self,
        libraries: dict[str, list[Game]],
        errors: dict[str, str],
        duration_ms: int,
        total_stores: int,
    ) -> SyncResult:
        """Build the final ``SyncResult`` + log the summary line.

        Partial-success heuristic: ``success=True`` if at least
        one store contributed.
        """
        merged = self._flatten(libraries)  # type: ignore[attr-defined]  # provided by _SyncQueriesMixin
        logger.info(
            "[SyncService] sync complete — %d games across %d stores "
            "in %dms (%d errors)",
            len(merged), len(libraries), duration_ms, len(errors),
        )
        return SyncResult(
            success=len(errors) < total_stores,
            games=merged,
            count=len(merged),
            duration_ms=duration_ms,
            error=None if not errors else f"{len(errors)}_stores_failed",
        )
