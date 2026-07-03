"""services/launch_logs.py — Async facade for the launch-log archive.

The actual log reading / exporting logic lives in
:mod:`unifideck.launcher.diagnostics.log_archive` as synchronous
functions because launches happen out-of-process (via the
``bin/unifideck-launcher`` binary) and the archive is plain
files on disk — no event-loop affinity required.

This service is the **plugin-side** wrapper: it exposes ``read``
and ``export`` as ``async`` methods (thread-hopped via
``asyncio.to_thread``) so the RPC layer can call them without
blocking the event loop, and threads the ``ConfigManager``
dependency through to ``log_archive`` without each RPC site
having to know about it.

The matching RPC surface lives in
:mod:`unifideck.rpc.mixins.launch`: ``get_launch_logs`` calls
:meth:`read`, ``export_launch_logs`` calls :meth:`export`.

This file was added to fix a gap detected during the RPC audit:
the mixin referenced ``self.services.launch_logs`` as if it
were already wired, but no service class existed. The container
attribute, ``_SERVICE_DEFS`` row, and this module were added in
one batch so the RPC surface works end-to-end.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from unifideck.launcher.diagnostics import log_archive

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)


class LaunchLogsService:
    """Async facade exposing the launch-log archive over RPC.

    Single-purpose container for the ``config`` dependency that
    ``log_archive``'s functions need (they take ``config`` as a
    positional argument to resolve the archive directory). Holding
    ``config`` on the service spares each RPC handler the trouble
    of plumbing the manager through.

    Both methods do their I/O via :func:`asyncio.to_thread` so a
    large log file or a slow filesystem doesn't stall the event
    loop and starve other RPC traffic.
    """

    def __init__(self, config: ConfigManager | None = None) -> None:
        """Initialise with an optional :class:`ConfigManager`.

        ``config`` is allowed to be ``None`` so the service stays
        constructable from minimal test harnesses; ``log_archive``
        itself tolerates ``None`` and falls back to the default
        archive path. In production the bootstrap always supplies
        a real manager.
        """
        self._config = config

    async def read(self, launch_id: str, max_lines: int = 500) -> dict[str, Any]:
        """Tail the archived log for ``launch_id``.

        Returns a dict with ``exists``, ``path``, ``lines``, and
        ``total`` keys — see :func:`log_archive.read_launch_logs`
        for the full shape. ``max_lines`` caps the tail size so a
        runaway log doesn't blow up the RPC payload; the default
        of 500 matches the UI's log viewer height.
        """
        return await asyncio.to_thread(
            log_archive.read_launch_logs,
            launch_id,
            self._config,
            max_lines=max_lines,
        )

    async def export(self, launch_id: str, dest_path: str = "") -> dict[str, Any]:
        """Copy the archived log for ``launch_id`` to ``dest_path``.

        Delegates to :func:`log_archive.export_launch_logs`, which
        falls back to a default destination (``~/unifideck-logs/``)
        when ``dest_path`` is empty. The return dict carries
        ``success`` plus the resolved destination so the UI can
        show "Exported to: …" without recomputing the path.
        """
        return await asyncio.to_thread(
            log_archive.export_launch_logs,
            launch_id,
            dest_path,
            self._config,
        )
