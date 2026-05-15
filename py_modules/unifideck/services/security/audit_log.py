"""services.security.audit_log — Bounded audit log + per-event counters.

Extracted from the flat ``security_service.py`` on 2026-04-18 to
encapsulate the two state pieces that ``SecurityService``
maintains for observability:

  - A bounded deque of recent event entries (for the
    DiagnosticsPanel timeline, exposed via the ``get_audit_log``
    RPC).
  - A per-event-type counter map (for the Security metrics row,
    exposed via ``get_counters``).

The class owns these two pieces together because they are
updated in lockstep: every ``record()`` call appends to the deque
and bumps the counter. Separating them would just duplicate the
exception-catching boilerplate on both sides.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    """Bounded audit log with per-event-type counters.

    The deque has a maxlen so old entries are dropped automatically
    — the UI only shows the most recent ones anyway, and we don't
    want the log to grow unbounded over long sessions. Counters
    persist across truncation (they're a cumulative total since
    the service started, not a sliding window).
    """

    def __init__(self, capacity: int) -> None:
        """Initialise with the given deque capacity.

        Args:
            capacity: Maximum number of entries kept in the log.
                Configured via ``security.audit_log_capacity``
                (default 500).
        """
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counters: dict[str, int] = {}

    def record(self, event_name: str, payload: dict[str, Any]) -> None:
        """Append an entry to the audit log and bump the counter.

        Best-effort: a failure to record is logged at debug level
        but never raises. The whole point is observability, so a
        failure to *observe* can't be allowed to break the code
        path being observed.
        """
        try:
            entry = {
                "event": event_name,
                "timestamp": time.time(),
                "payload": dict(payload),
            }
            self._entries.append(entry)
            self._counters[event_name] = (
                self._counters.get(event_name, 0) + 1
            )
        except Exception as e:
            logger.debug(
                "[AuditLog] record failed: %s", e,
            )

    def snapshot(
        self, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return a newest-first snapshot of the audit log.

        If ``limit`` is provided and positive, truncate to the
        most recent ``limit`` entries. Otherwise return all.
        """
        entries = list(reversed(self._entries))
        if limit is not None and limit > 0:
            entries = entries[:limit]
        return entries

    def counters(self) -> dict[str, int]:
        """Return a copy of the per-event-type counters."""
        return dict(self._counters)

    def clear(self) -> None:
        """Wipe both the log and the counters.

        Called by ``SecurityService.clear_audit_log`` after
        operator review. Does NOT reset policy-level state like
        the brute-force detector — those are in their own classes.
        """
        self._entries.clear()
        self._counters.clear()
