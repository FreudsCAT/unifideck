"""``@safe_file_op`` decorator — uniform OSError handling for file ops.

OP-08c1 | py_modules/unifideck/core/io/safe_file_op.py

Most file operations across the plugin (config reads, cache
loads, manifest writes) have the same fallback contract:
``OSError`` → log + return a default. Without this decorator
every call site has to wrap with try/except, which makes the
code unreadable.

Usage::

    @safe_file_op(default=[])
    def list_games(path: str) -> list[str]: ...

The decorator detects whether the wrapped function is sync
or async and applies the right wrapper. The first positional
arg is captured as ``path_hint`` for the log line — relies on
the convention that file ops take the path first.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")
_Callable = Callable[..., T | Awaitable[T]]


def safe_file_op(
    default: Any = None,
    *,
    log_level: int = logging.WARNING,
) -> Callable[[_Callable], _Callable]:
    """Decorator factory that wraps a file op with ``OSError`` handling.

    Returns the actual decorator (the factory pattern lets
    callers configure the default value + log level per call
    site). The decorator inspects ``fn``: if it's a
    coroutine function, builds an async wrapper; otherwise
    builds a sync wrapper. Both wrappers have identical
    semantics — try, catch ``OSError``, log, return default.

    Only ``OSError`` is caught — other exceptions (logic
    errors, unexpected types) propagate normally. This is
    deliberate: ``OSError`` covers the legitimate "filesystem
    said no" cases (file missing, permission denied, disk
    full, broken symlink) without swallowing programmer
    bugs.

    Args:
        default: value to return on ``OSError``. Defaults
            to ``None``; callers typically pass ``[]`` or
            ``{}`` for collection-returning ops.
        log_level: ``logging`` level for the failure log
            line. Defaults to WARNING; pass DEBUG for ops
            that fail noisily and harmlessly (e.g. cache
            misses).

    Returns:
        The actual decorator, ready to apply to a function.
    """

    def decorator(fn: _Callable) -> _Callable:
        """Pick async or sync wrapper based on ``fn``'s nature.

        ``asyncio.iscoroutinefunction`` is checked at
        decoration time (not call time) so wrapper
        selection is one-shot per decorated function — no
        per-call overhead.

        Args:
            fn: function to wrap (sync or async).

        Returns:
            Wrapped function with identical signature.
        """
        fname = getattr(fn, "__name__", repr(fn))
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                """Await ``fn``; on OSError log + return default.

                The path-hint extraction
                (``args[0] or kwargs["path"]``) reflects the
                project convention: file ops always take
                their path as first positional or as a
                ``path`` kwarg. Doesn't reach further into
                kwargs to keep the logic predictable.

                Args:
                    *args / **kwargs: forwarded to ``fn``.

                Returns:
                    ``fn``'s return value, or ``default`` on
                    OSError.
                """
                try:
                    return await fn(*args, **kwargs)
                except OSError as e:
                    path_hint = args[0] if args else kwargs.get("path", "?")
                    logger.log(
                        log_level,
                        "[safe_file_op] %s(%r) failed: %s: %s",
                        fname,
                        path_hint,
                        type(e).__name__,
                        e,
                    )
                    return default

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Call ``fn`` sync; on OSError log + return default.

            Same logic as ``async_wrapper`` but without the
            await — used when the wrapped function is a
            plain ``def``.

            Args:
                *args / **kwargs: forwarded to ``fn``.

            Returns:
                ``fn``'s return value, or ``default`` on
                OSError.
            """
            try:
                return fn(*args, **kwargs)
            except OSError as e:
                path_hint = args[0] if args else kwargs.get("path", "?")
                logger.log(
                    log_level,
                    "[safe_file_op] %s(%r) failed: %s: %s",
                    fname,
                    path_hint,
                    type(e).__name__,
                    e,
                )
                return default

        return sync_wrapper

    return decorator
