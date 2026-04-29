"""Base class for RPC handler groups.

OP-25a | py_modules/unifideck/rpc/handlers/base.py
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RpcHandlerBase:
    """Base for handler groups that are grafted onto Plugin via ``bind_handlers``."""

    def __init__(
        self,
        *,
        bus: Any,
        registry: Any,
        cache: Any,
        config: Any,
        sync_service: Any,
        services: Any,
    ) -> None:
        self._bus = bus
        self._registry = registry
        self._cache = cache
        self._config = config
        self._sync = sync_service
        self._services = services

    @staticmethod
    def _require(svc: T | None, name: str) -> T:
        """Return *svc* or raise ``RpcError("service_unavailable")``."""
        if svc is None:
            raise RpcError("service_unavailable", service=name)
        return svc

    def handler_methods(self) -> list[str]:
        """Return names of every public async method defined on this instance."""
        return [
            name
            for name in dir(self)
            if not name.startswith("_")
            and name != "handler_methods"
            and asyncio.iscoroutinefunction(getattr(self, name, None))
        ]
