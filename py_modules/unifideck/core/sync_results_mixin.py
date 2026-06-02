"""Result-aggregation mixin for :class:`SyncService`.

Extracted from ``core/sync_service.py`` to keep the host file
under the 550 LOC volumetry cap. Single responsibility now:
build the final ``SyncResult`` from per-store libraries +
errors + timings, and log the summary line.

Earlier revisions of this module also housed
``_apply_dedup_and_emit`` and ``_tracked_stores`` for
cross-store dedup. Dedup was removed (duplicate titles across
stores now show as distinct shortcuts thanks to the
store-scoped ``generate_app_id`` identity); the dead code was
deleted with the appid-generation refactor.
"""

from __future__ import annotations

import logging

from .types import Game, SyncResult

logger = logging.getLogger(__name__)


class _SyncResultsMixin:
    """Result helpers for :class:`SyncService`."""

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
