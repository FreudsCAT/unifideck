"""unifideck.rpc — RPC infrastructure and mixins."""
from __future__ import annotations

from .auto_wire import auto_wrap_rpc_methods
from .errors import RpcError
from .wrapper import rpc_wrapper

__all__ = ["RpcError", "auto_wrap_rpc_methods", "rpc_wrapper"]
