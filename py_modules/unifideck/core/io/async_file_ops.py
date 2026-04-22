"""core/io/async_file_ops.py — Non-blocking filesystem I/O.

# OP-06a | core/io/async_file_ops.py | Depends: (none)

Every sync stdlib call (``open``, ``Path.exists``, ``os.makedirs``,
``shutil.copy``, ...) is wrapped in ``asyncio.to_thread`` so the
Decky event loop is never blocked on disk access. Use via the
conventional alias::

    from unifideck.core.io import async_file_ops as aio
"""
import os
from typing import Any

PathLike = str | os.PathLike


async def exists(path: PathLike) -> bool:
    """Return True if ``path`` exists (file or directory)."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + os.path.exists")


async def is_file(path: PathLike) -> bool:
    """Return True if ``path`` is a regular file."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + os.path.isfile")


async def is_dir(path: PathLike) -> bool:
    """Return True if ``path`` is a directory."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + os.path.isdir")


async def listdir(path: PathLike) -> list[str]:
    """Return directory entries. Empty list if missing or unreadable."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + os.listdir")


async def stat(path: PathLike) -> os.stat_result | None:
    """Return ``os.stat_result`` or None if ``path`` is inaccessible."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + os.stat")


async def makedirs(path: PathLike, mode: int = 0o755, exist_ok: bool = True) -> bool:
    """Create ``path`` (and missing parents). Return True on success."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + os.makedirs")


async def ensure_dir(path: PathLike) -> bool:
    """Thin alias for ``makedirs(path, exist_ok=True)``."""
    raise NotImplementedError("OP-06a: implement using makedirs")


async def copy(src: PathLike, dst: PathLike) -> bool:
    """Copy ``src`` → ``dst`` preserving metadata. Return True on success."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + shutil.copy2")


async def move(src: PathLike, dst: PathLike) -> bool:
    """Move ``src`` → ``dst``. Return True on success."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + shutil.move")


async def remove(path: PathLike) -> bool:
    """Delete a file or directory tree at ``path``. Return True on success."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + shutil.rmtree/os.remove")


async def read_text(path: PathLike, encoding: str = "utf-8") -> str | None:
    """Read text file. Return None on any OSError or decode failure."""
    raise NotImplementedError("OP-06a: implement using asyncio.to_thread + open().read()")


async def write_text(
    path: PathLike,
    content: str,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> bool:
    """Atomic write via tmp + rename. ``mode`` applies chmod after write."""
    raise NotImplementedError("OP-06a: implement _write_text_sync via asyncio.to_thread")


def _write_text_sync(
    path: PathLike,
    content: str,
    encoding: str,
    mode: int | None,
) -> bool:
    """Sync implementation of ``write_text``; invoked via to_thread.

    Write to ``<path>.tmp`` first, chmod if requested, then rename over
    the target so readers never see a partial file.
    """
    raise NotImplementedError("OP-06a: implement atomic tmp+rename write")


async def write_bytes(path: PathLike, data: bytes) -> bool:
    """Atomic bytes write via tmp + rename. Return True on success."""
    raise NotImplementedError("OP-06a: implement _write_bytes_sync via asyncio.to_thread")


def _write_bytes_sync(path: PathLike, data: bytes) -> bool:
    """Sync implementation of ``write_bytes``; invoked via to_thread."""
    raise NotImplementedError("OP-06a: implement atomic bytes write")


async def read_json(path: PathLike) -> dict[str, Any]:
    """Read + parse JSON. Return empty dict on any failure (missing,
    malformed, unreadable) — callers can't distinguish these cases,
    so wrap with explicit try/except if that matters.
    """
    raise NotImplementedError("OP-06a: implement _read_json_sync via asyncio.to_thread")


def _read_json_sync(path: PathLike) -> dict[str, Any]:
    """Sync implementation of ``read_json``; invoked via to_thread."""
    raise NotImplementedError("OP-06a: implement json.load with error handling")


async def write_json(
    path: PathLike,
    data: dict[str, Any],
    indent: int = 2,
    mode: int | None = None,
) -> bool:
    """Serialize ``data`` and atomic-write as JSON. Return True on success."""
    raise NotImplementedError("OP-06a: implement _write_json_sync via asyncio.to_thread")


def _write_json_sync(
    path: PathLike,
    data: dict[str, Any],
    indent: int,
    mode: int | None,
) -> bool:
    """Sync implementation of ``write_json``; invoked via to_thread."""
    raise NotImplementedError("OP-06a: implement json.dump with atomic write")
