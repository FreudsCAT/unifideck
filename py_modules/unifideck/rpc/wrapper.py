"""RPC method wrapper — envelope serialisation and error handling.

OP-24b | py_modules/unifideck/rpc/wrapper.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import functools
import logging
from typing import Any, TypeVar

from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)
F = TypeVar("F")


def _serialize(value: Any) -> Any:
    """Recursively convert dataclass instances to plain dicts."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _to_envelope(value: Any) -> dict[str, Any]:
    """Coerce a handler return value into the standard RPC envelope."""
    if isinstance(value, dict) and "success" in value:
        return value
    if isinstance(value, dict):
        return {"success": True, **value}
    return {"success": True, "data": value}


def rpc_wrapper(func: F) -> F:
    """Decorator that wraps an async RPC method with envelope + error handling."""
    if not asyncio.iscoroutinefunction(func):
        raise TypeError(f"rpc_wrapper requires an async function, got {func!r}")
    if getattr(func, "__rpc_wrapped__", False):
        return func  # type: ignore[return-value]

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = await func(*args, **kwargs)
            return _to_envelope(_serialize(result))
        except RpcError as exc:
            return {"success": False, "error": exc.code, "data": exc.context}
        except Exception as exc:
            logger.exception("RPC %s failed", func.__qualname__)
            return {
                "success": False,
                "error": "internal_error",
                "data": {"detail": repr(exc)},
            }

    wrapper.__rpc_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]
