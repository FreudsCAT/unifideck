"""rpc/auto_wire.py — Auto-wrap Plugin class methods for RPC.
# OP-24c | rpc/auto_wire.py | Depends: OP-24b
"""
from __future__ import annotations


def auto_wrap_rpc_methods(cls):
    """Class decorator: wraps all async def methods with RPC error handling.
    Applied to Plugin class via @auto_wrap_rpc_methods.
    """
    raise NotImplementedError("OP-24c: wrap each async method with try/except + to_dict")
