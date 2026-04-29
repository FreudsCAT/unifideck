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
        return self._require(self._services.security, "security")

    async def get_security_audit_log(self, limit: int = 100) -> Any:
        return self._security().get_audit_log(limit=limit)

    async def get_security_counters(self) -> Any:
        return self._security().get_counters()

    async def get_security_bruteforce_status(self) -> Any:
        return self._security().get_bruteforce_status()

    async def clear_security_audit_log(self) -> Any:
        self._security().clear_audit_log()
        return {"success": True}

    async def reset_security_bruteforce(self) -> Any:
        self._security().reset_bruteforce_state()
        return {"success": True}
