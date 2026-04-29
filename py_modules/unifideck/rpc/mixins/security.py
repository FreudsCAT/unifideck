"""Security RPC mixin for Plugin class.

OP-26b | rpc/mixins/security.py
"""
from __future__ import annotations

from typing import Any

from unifideck.rpc.errors import RpcError


class SecurityRPCMixin:
    """Security audit log, counters, and brute-force management."""

    services: Any

    def _require_security(self) -> Any:
        svc = getattr(self.services, "security", None)
        if svc is None:
            raise RpcError("service_unavailable", service="security")
        return svc

    async def get_security_audit_log(self, limit: int = 100) -> Any:
        return self._require_security().get_audit_log(limit=limit)

    async def get_security_counters(self) -> Any:
        return self._require_security().get_counters()

    async def get_security_bruteforce_status(self) -> Any:
        return self._require_security().get_bruteforce_status()

    async def clear_security_audit_log(self) -> Any:
        self._require_security().clear_audit_log()
        return {"success": True}

    async def reset_security_bruteforce(self) -> Any:
        self._require_security().reset_bruteforce_state()
        return {"success": True}
