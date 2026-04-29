"""core/io/safe_file_op.py — Error-handling decorator for file I/O.

Moved from core/ to the new core/io/ subpackage,
paired with async_file_ops.py. The decorator is specifically
designed to wrap coroutines from the sibling async_file_ops
module; colocating them documents that coupling. Clean break:
no shim in core/.


    try:
        return await _do_the_thing(path, ...)
    except (OSError, PermissionError) as e:
        logger.warning("[async_file_ops] %s failed: %s", path, e)
        return None

This decorator captures that pattern exactly once:

    @safe_file_op(default=None)
    async def read_text(path: str) -> Optional[str]:
        return (await aiofiles_like_read(path)).decode()

The decorator logs at WARNING with the function name and the
first positional argument (conventionally the path) so the log
line always identifies which file triggered the failure.

Design choices:
  - `default` is the sentinel returned on any caught exception —
    typically None for readers, False for writers. Explicit
    default avoids the "silent None everywhere" antipattern.
  - The caught exception set is `(OSError,)` since PermissionError
    is already a subclass of OSError — catching both was redundant
    in the legacy code but preserved here as a docstring note.
  - `FileNotFoundError` is also an OSError subclass — if a caller
    wants to distinguish "missing" from "broken", they should NOT
    use this decorator and catch explicitly.
  - The decorator supports both sync and async callables so it
    can wrap helpers in cache_manager, config_persistence, etc.
    in later refactors.

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
    """Return a decorator that catches OSError and returns `default`.

    Works transparently on both sync and async callables: the
    wrapper detects the coroutine-ness of the wrapped function at
    decoration time and returns the matching wrapper shape.

    Args:
      default: value returned on any caught exception. Pick None
        for readers ("no data"), False for writers ("write failed
        gracefully"), or {} / [] for collection builders.
      log_level: level at which exceptions are logged. Defaults
        to WARNING — production deployments want to see these in
        the Decky log but not treat them as critical. Use ERROR
        if a specific operation is considered fatal.

    Usage:
      @safe_file_op(default=None)
      async def read_text(path: str) -> Optional[str]:
          ...

      @safe_file_op(default=False)
      def write_bytes(path: str, data: bytes) -> bool:
          ...

    """

    def decorator(fn: _Callable) -> _Callable:
        # First positional arg is conventionally the path — we
        # capture it for the log message so callers see which
        # file triggered the failure without wiring up extra args.
        fname = getattr(fn, "__name__", repr(fn))

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except OSError as e:
                    # PermissionError, FileNotFoundError, IsADirectoryError
                    # etc. are all OSError subclasses and land here.
                    path_hint = args[0] if args else kwargs.get("path", "?")
                    logger.log(
                        log_level,
                        "[safe_file_op] %s(%r) failed: %s: %s",
                        fname, path_hint, type(e).__name__, e,
                    )
                    return default
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except OSError as e:
                path_hint = args[0] if args else kwargs.get("path", "?")
                logger.log(
                    log_level,
                    "[safe_file_op] %s(%r) failed: %s: %s",
                    fname, path_hint, type(e).__name__, e,
                )
                return default
        return sync_wrapper

    return decorator
