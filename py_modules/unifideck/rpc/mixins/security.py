"""SecurityRPCMixin — audit log + brute-force state RPC.

OP-26f | py_modules/unifideck/rpc/mixins/security.py

Mixin equivalent of ``SecurityHandlers`` (OP-25f). Same five
methods, same shape — surfaces ``SecurityService``'s audit
log + brute-force state for the QAM security tab.

The private ``_require_security`` helper centralises the
service-availability check so each public method stays
terse.
"""

from __future__ import annotations

from typing import Any

from unifideck.rpc import RpcError


class SecurityRPCMixin:
    """Audit-log + brute-force RPC, mixed into the plugin class."""

    services: Any

    async def get_security_audit_log(self, limit: int = 100) -> Any:
        """Return the most recent ``limit`` audit-log entries.

        Newest-first ordering. Used by the security tab to
        render the live audit table.

        Args:
            limit: cap on entries returned. Default 100 —
                matches the typical tab page size.

        Returns:
            List of audit entry dicts.
        """
        security = self._require_security()
        return security.get_audit_log(limit=limit)

    async def get_security_counters(self) -> Any:
        """Return per-event-kind cumulative counters.

        Counters are session-scoped (restart wipes them)
        since the audit log itself is session-scoped.

        Returns:
            ``{event_kind → count}`` dict.
        """
        security = self._require_security()
        return security.get_counters()

    async def get_security_bruteforce_status(self) -> Any:
        """Return the live brute-force detector state.

        Snapshot includes the rolling failure count, window
        size, both thresholds, and the ``escalated`` flag.

        Returns:
            Dict from ``BruteForceDetector.status``.
        """
        security = self._require_security()
        return security.get_bruteforce_status()

    async def clear_security_audit_log(self) -> Any:
        """Empty the audit log buffer.

        Admin-only: the clear is logged at INFO (in the
        service) for traceability. Counters wipe at the
        same time.

        Returns:
            ``{success: True}``.
        """
        security = self._require_security()
        security.clear_audit_log()
        return {"success": True}

    async def reset_security_bruteforce(self) -> Any:
        """Reset the brute-force detector's rolling failure window.

        Clears every recent failure and the escalation
        flag. Used by the "admin reset" button.

        Returns:
            ``{success: True}``.
        """
        security = self._require_security()
        security.reset_bruteforce_state()
        return {"success": True}

    def _require_security(self) -> Any:
        """Return the security service or raise ``service_unavailable``.

        Centralises the null check so each public method
        doesn't have to repeat the validation.

        Returns:
            The ``SecurityService`` instance.

        Raises:
            RpcError: ``code="service_unavailable"``,
                ``service="security"`` when the service
                isn't wired.
        """
        if self.services.security is None:
            raise RpcError(
                "service_unavailable",
                service="security",
            )
        return self.services.security
