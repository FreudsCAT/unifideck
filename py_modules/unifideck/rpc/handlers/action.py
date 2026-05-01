"""Action RPC handlers.

OP-25b | py_modules/unifideck/rpc/handlers/action.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.rpc.errors import RpcError
from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)


class ActionHandlers(RpcHandlerBase):
    """Dispatch ``unifideck://`` action URIs to backend handlers."""

    async def dispatch_unifideck_action(self, uri: str) -> Any:
        """Parse a ``unifideck://`` URI and execute its handler."""
        from unifideck.actions.dispatch import dispatch_backend_action

        return await dispatch_backend_action(
            uri=uri,
            registry=self._registry,
            sync_service=self._sync,
            services=self._services,
        )
