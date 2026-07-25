"""support_bundle/service.py — Async facade over the collector.

Mirrors :class:`~unifideck.services.launch_logs.LaunchLogsService`: the
real work is synchronous file I/O, so it runs in a worker thread via
``asyncio.to_thread`` and this class exists to hold the ``config`` and
``paths`` dependencies plus the re-entrancy lock.

The lock matters. A capture reads a few MB, compresses it, and writes a
zip; two concurrent runs would double that I/O and race on the output
filename. The second caller gets ``in_progress`` back instead of a
second archive.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from . import collect

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.services.bootstrap.paths import ServicePaths

logger = logging.getLogger(__name__)


class SupportBundleService:
    """Collect logs and diagnostics into a single zip archive."""

    def __init__(
        self,
        config: ConfigManager | None = None,
        paths: ServicePaths | None = None,
    ) -> None:
        """Hold the dependencies the collector needs.

        Both are optional so a minimal test harness can construct the
        service; the collector tolerates ``None`` and falls back to
        default locations, exactly as the launch-log archive does.
        """
        self._config = config
        self._paths = paths
        self._lock = asyncio.Lock()

    async def capture(
        self, dest_path: str = "", extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a diagnostic bundle and return its description.

        Args:
            dest_path: Optional destination directory or ``.zip`` path.
                Empty means "use ``logs.export_path``, then the usual
                fallbacks" — it never degrades into writing a bare file
                into the home directory.
            extra: Facts only the RPC layer can see (feature flags,
                frontend runtime probes), folded into the environment
                report.

        Returns:
            The collector payload, or ``{"in_progress": True}`` when a
            capture is already running.

        Raises:
            OSError: No writable destination, or the archive could not
                be created. The RPC layer maps this to a typed error.
        """
        if self._lock.locked():
            logger.info("[support_bundle] capture already running")
            return {"in_progress": True, "archive_path": None}
        async with self._lock:
            return await asyncio.to_thread(
                collect.capture_bundle,
                dest_path,
                self._config,
                self._paths,
                extra,
            )
