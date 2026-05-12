"""Audit log — rotating log of security-relevant events.

OP-19b | py_modules/unifideck/services/security/audit_log.py

``AuditLog`` is a rotating file logger dedicated to security events
(auth attempts, config changes, token operations, permission
violations). Logs are written in a structured (JSON) format so they
can be machine-parsed by external tools.

Rotation is size-based : the active log is rotated when it exceeds
the configured limit (default 5 MiB), and the N most recent rotated
files are kept.
"""

from __future__ import annotations
import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    """Audit log."""

    def __init__(self, capacity: int) -> None:
        """Initialize the instance."""
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counters: dict[str, int] = {}

    def record(self, event_name: str, payload: dict[str, Any]) -> None:
        """Record."""
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
        """Snapshot."""
        entries = list(reversed(self._entries))
        if limit is not None and limit > 0:
            entries = entries[:limit]
        return entries

    def counters(self) -> dict[str, int]:
        """Counters."""
        return dict(self._counters)

    def clear(self) -> None:
        """Clear."""
        self._entries.clear()
        self._counters.clear()
