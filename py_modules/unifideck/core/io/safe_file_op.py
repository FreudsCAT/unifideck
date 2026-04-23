"""core/io/safe_file_op.py — Error-handling decorator for file I/O.

# OP-06b | core/io/safe_file_op.py | Depends: (none)

Captures the canonical ``try/except OSError → log + return
default`` pattern exactly once. Designed to wrap sibling
``async_file_ops`` coroutines, but works on sync callables too.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")
_Callable = Callable[..., T | Awaitable[T]]


def safe_file_op(
    default: Any = None,
    *,
    log_level: int = 30,  # logging.WARNING
) -> Callable[[_Callable], _Callable]:
    """Decorator factory: catch ``OSError`` subclasses, return ``default``.

    Auto-detects sync vs async at decoration time and returns the
    matching wrapper shape. Logs at ``log_level`` with the function
    name + first positional arg (conventionally the path) so the
    log line identifies which file triggered the failure. Pick
    ``None`` as default for readers, ``False`` for writers.
    """
    def decorator(fn: _Callable) -> _Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except OSError as e:
                    path_hint = args[0] if args else "?"
                    logging.log(log_level, "%s(%s): %s", fn.__name__, path_hint, e)
                    return default
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except OSError as e:
                    path_hint = args[0] if args else "?"
                    logging.log(log_level, "%s(%s): %s", fn.__name__, path_hint, e)
                    return default
            return sync_wrapper  # type: ignore[return-value]
    return decorator
