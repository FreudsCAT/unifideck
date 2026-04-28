"""py_modules/unifideck/core/io/ — Filesystem I/O primitives.

Consolidated. This subpackage owns the low-level,
non-blocking filesystem primitives used throughout the Unifideck
backend:

  - async_file_ops : asyncio wrappers around blocking stdlib calls
                      (open, exists, makedirs, copy, rmdir, ...).
                      Every file operation inside an ``async def``
                      method in Unifideck goes through these
                      wrappers so the single asyncio event loop
                      Decky Loader runs on is never blocked by disk
                      I/O. Importing convention across the codebase::

                          from unifideck.core.io import async_file_ops as aio

  - safe_file_op : error-handling decorator that captures the
                      canonical ``try/except OSError → log + return
                      default`` pattern exactly once. Designed to
                      wrap coroutines from async_file_ops (which is
                      why the two modules live side-by-side in this
                      subpackage).

Clean break: the previous locations unifideck.core.async_file_ops
and unifideck.core.safe_file_op no longer exist. Every callsite
has been rewritten. If you hit an ``ImportError`` on either of
those names, you are on a pre-17f checkout — update your imports
to unifideck.core.io.
"""
from . import async_file_ops  # noqa: F401
from .safe_file_op import safe_file_op

__all__ = [
    "async_file_ops",
    "safe_file_op",
]
