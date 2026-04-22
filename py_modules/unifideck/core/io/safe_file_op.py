"""core/io/safe_file_op.py — Error-handling decorator for file I/O.

# OP-06b | core/io/safe_file_op.py | Depends: (none)

Captures the canonical ``try/except OSError → log + return
default`` pattern exactly once. Designed to wrap sibling
``async_file_ops`` coroutines, but works on sync callables too.
"""
from __future__ import annotations

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
    raise NotImplementedError("OP-06b: implement sync/async decorator factory")
