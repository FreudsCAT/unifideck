"""Auto-wrap Plugin class methods for RPC.

OP-24c | py_modules/unifideck/rpc/auto_wire.py
"""
from __future__ import annotations

import asyncio

from unifideck.rpc.wrapper import rpc_wrapper


def auto_wrap_rpc_methods(cls: type) -> type:
    """Class decorator: apply ``rpc_wrapper`` to every public async method."""
    seen: set[str] = set()
    for klass in cls.__mro__:
        for name, attr in vars(klass).items():
            if name.startswith("_") or name in seen:
                continue
            seen.add(name)
            if asyncio.iscoroutinefunction(attr) and not getattr(
                attr, "__rpc_wrapped__", False
            ):
                setattr(cls, name, rpc_wrapper(attr))
    return cls
