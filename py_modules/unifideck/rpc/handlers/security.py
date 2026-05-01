"""Security RPC handlers.

OP-25f | py_modules/unifideck/rpc/handlers/security.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)


class SecurityHandlers(RpcHandlerBase):
    """Security audit log, counters, and brute-force management."""

    def _security(self) -> Any:
        """Return the security service, raising if unavailable."""
        return self._require(self._services.security, "security")

    async def get_security_audit_log(self, limit: int = 100) -> Any:
        """Return recent security audit log entries."""
        return self._security().get_audit_log(limit=limit)

    async def get_security_counters(self) -> Any:
        """Return security event counters."""
        return self._security().get_counters()

    async def get_security_bruteforce_status(self) -> Any:
        """Return current brute-force lockout state."""
        return self._security().get_bruteforce_status()

    async def clear_security_audit_log(self) -> Any:
        """Clear the security audit log."""
        self._security().clear_audit_log()
        return {"success": True}

    async def reset_security_bruteforce(self) -> Any:
        """Reset brute-force lockout counters."""
        self._security().reset_bruteforce_state()
        return {"success": True}
