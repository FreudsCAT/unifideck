"""Action RPC mixin for Plugin class.

OP-26k | rpc/mixins/action.py
"""
from __future__ import annotations

from typing import Any


class ActionRPCMixin:
    """Dispatch ``unifideck://`` action URIs."""

    registry: Any
    services: Any

    async def dispatch_unifideck_action(self, uri: str) -> Any:
        """Parse a ``unifideck://`` URI and execute its handler."""
        from unifideck.actions.dispatch import dispatch_backend_action

        return await dispatch_backend_action(
            uri=uri,
            registry=self.registry,
            cloudsave=self.services.cloudsave,
            sync_service=getattr(self, "sync_service", None),
        )
