"""unifideck.rpc.handlers — RPC handler groups."""
from __future__ import annotations

from unifideck.rpc.handlers.action import ActionHandlers
from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.handlers.download import DownloadHandlers
from unifideck.rpc.handlers.launch import LaunchHandlers
from unifideck.rpc.handlers.observability import ObservabilityHandlers
from unifideck.rpc.handlers.security import SecurityHandlers
from unifideck.rpc.handlers.store import StoreHandlers
from unifideck.rpc.handlers.ui import UIHandlers

__all__ = [
    "ActionHandlers",
    "DownloadHandlers",
    "LaunchHandlers",
    "ObservabilityHandlers",
    "RpcHandlerBase",
    "SecurityHandlers",
    "StoreHandlers",
    "UIHandlers",
]
