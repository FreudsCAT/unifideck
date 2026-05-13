"""SecurityHandlers — audit log + brute-force state RPC.

OP-25f | py_modules/unifideck/rpc/handlers/security_handlers.py

Thin facade over ``SecurityService`` (OP-19a) for the
security tab in the QAM:

* read the audit log (with limit) + per-event-kind counters;
* read the live brute-force detector status;
* admin actions: clear the audit log, reset the brute-force
  counters.

Every method goes through ``_require`` so a missing security
service surfaces a typed ``service_unavailable`` error.
"""

from __future__ import annotations

from typing import Any, cast

from unifideck.rpc.handlers.base import RpcHandlerBase


class SecurityHandlers(RpcHandlerBase):
    """Security-audit + brute-force RPC surface."""

    async def get_security_audit_log(self, limit: int = 100) -> Any:
        """Return the most recent ``limit`` audit-log entries.

        Newest-first ordering (preserved from
        ``AuditLog.snapshot``). Used by the security tab to
        render the live audit table.

        Args:
            limit: cap on entries returned. Default 100 —
                matches the typical tab page size.

        Returns:
            List of audit entry dicts.
        """
        svc = self._require(self._services.security, "security")
        return cast(list, svc.get_audit_log(limit=limit))

    async def get_security_counters(self) -> Any:
        """Return per-event-kind cumulative counters.

        Used by the "events at a glance" widget. The
        counters are lifetime (session-scoped — restart
        wipes them) since the audit log itself is
        session-scoped.

        Returns:
            ``{event_kind → count}`` dict.
        """
        svc = self._require(self._services.security, "security")
        return cast(dict, svc.get_counters())

    async def get_security_bruteforce_status(self) -> Any:
        """Return the live brute-force detector state.

        Snapshot includes the rolling failure count, window
        size, both thresholds, and the ``escalated`` flag.

        Returns:
            Dict from ``BruteForceDetector.status``.
        """
        svc = self._require(self._services.security, "security")
        return cast(dict, svc.get_bruteforce_status())

    async def clear_security_audit_log(self) -> Any:
        """Empty the audit log buffer.

        Admin-only: the audit-log clear is logged
        loudly at INFO (in the service) so the cleanup is
        traceable in plugin logs. Counters are wiped at the
        same time so the UI doesn't show stale totals.

        Returns:
            ``{success: True}``.
        """
        svc = self._require(self._services.security, "security")
        svc.clear_audit_log()
        return {"success": True}

    async def reset_security_bruteforce(self) -> Any:
        """Reset the brute-force detector's rolling failure window.

        Clears every recent failure and the escalation
        flag. Used by the "admin reset" button after a
        confirmed false-positive (e.g. test runs spamming
        auth attempts).

        Returns:
            ``{success: True}``.
        """
        svc = self._require(self._services.security, "security")
        svc.reset_bruteforce_state()
        return {"success": True}
