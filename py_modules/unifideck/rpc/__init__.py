# OP-24 | rpc/__init__.py | Depends: OP-24c
from __future__ import annotations
from .auto_wire import auto_wrap_rpc_methods

__all__ = ["auto_wrap_rpc_methods"]
