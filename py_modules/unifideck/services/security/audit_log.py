"""Audit log — in-memory rolling buffer of recent security events.

OP-19b | py_modules/unifideck/services/security/audit_log.py

``AuditLog`` is the in-process store of security-relevant events
(auth attempts, token operations, permission checks, config
validation). It's a bounded ``deque`` — older entries are evicted
when the configured capacity is reached.

Why in-memory rather than file-backed? The audit log is a
diagnostic aid for the user (visible in the QAM panel), not a
forensic-grade compliance log. A plugin restart wipes the log
intentionally — the user expects "what happened during this
session", not a permanent record. For higher-stakes auditing,
plugin logs (Decky-side) provide a separate file-backed trail.

Per-event-kind counters are maintained alongside the deque so the
UI can show totals without iterating the full buffer.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    """Bounded rolling buffer of audit entries + per-kind counters."""

    def __init__(self, capacity: int) -> None:
        """Build an empty audit log with the given capacity.

        Args:
            capacity: maximum number of entries retained. When
                the buffer is full, appending a new entry evicts
                the oldest one. Counters keep counting after
                eviction — the counter total can exceed the
                buffer size.
        """
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counters: dict[str, int] = {}

    def record(self, event_name: str, payload: dict[str, Any]) -> None:
        """Append a new audit entry.

        Wraps the entry with a timestamp + event name. A defensive
        try/except prevents an unserialisable payload (e.g.
        unexpected object types in a third-party event) from
        crashing the entire audit subsystem — instead the failure
        is logged at DEBUG and the entry is dropped.

        Args:
            event_name: short event identifier (e.g.
                ``"AUTH_FAILURE"``, ``"TOKEN_ENCRYPTED"``).
            payload: free-form dict of event-specific data; a
                shallow copy is taken so the caller can keep
                mutating their dict without affecting the log.
        """
        try:
            entry = {
                "event": event_name,
                "timestamp": time.time(),
                "payload": dict(payload),
            }
            self._entries.append(entry)
            self._counters[event_name] = self._counters.get(event_name, 0) + 1
        except Exception as e:
            logger.debug(
                "[AuditLog] record failed: %s",
                e,
            )

    def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return a copy of recent entries, newest first.

        Iteration is newest-first because the UI typically shows
        the most recent activity at the top. A shallow copy is
        returned so the caller can mutate the list without
        affecting the underlying deque.

        Args:
            limit: optional cap on the number of entries
                returned. ``None`` returns every entry.

        Returns:
            List of entry dicts newest-first.
        """
        entries = list(reversed(self._entries))
        if limit is not None and limit > 0:
            entries = entries[:limit]
        return entries

    def counters(self) -> dict[str, int]:
        """Return a snapshot of per-event-kind counters.

        Returns:
            Copy of the counter dict. Mutating the returned dict
            has no effect on the log.
        """
        return dict(self._counters)

    def clear(self) -> None:
        """Empty the buffer and reset every counter.

        Called by ``SecurityService.clear_audit_log`` on user
        request. Does not log here itself — the caller is
        responsible for logging the clear at INFO level.
        """
        self._entries.clear()
        self._counters.clear()
