"""core/io/async_file_ops.py — Non-blocking filesystem I/O.

# OP-06a | core/io/async_file_ops.py | Depends: (none)

Every sync stdlib call (``open``, ``Path.exists``, ``os.makedirs``,
``shutil.copy``, ...) is wrapped in ``asyncio.to_thread`` so the
Decky event loop is never blocked on disk access. Use via the
conventional alias::

    from unifideck.core.io import async_file_ops as aio
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

logger = logging.getLogger(__name__)

PathLike = str | os.PathLike


# ── Simple stat wrappers ─────────────────────────────────────────────


async def exists(path: PathLike) -> bool:
    """Return True if ``path`` exists (file or directory)."""
    try:
        return await asyncio.to_thread(os.path.exists, str(path))
    except OSError:
        return False


async def is_file(path: PathLike) -> bool:
    """Return True if ``path`` is a regular file."""
    try:
        return await asyncio.to_thread(os.path.isfile, str(path))
    except OSError:
        return False


async def is_dir(path: PathLike) -> bool:
    """Return True if ``path`` is a directory."""
    try:
        return await asyncio.to_thread(os.path.isdir, str(path))
    except OSError:
        return False


async def listdir(path: PathLike) -> list[str]:
    """Return directory entries. Empty list if missing or unreadable."""
    try:
        return await asyncio.to_thread(os.listdir, str(path))
    except OSError:
        return []


async def stat(path: PathLike) -> os.stat_result | None:
    """Return ``os.stat_result`` or None if ``path`` is inaccessible."""
    try:
        return await asyncio.to_thread(os.stat, str(path))
    except OSError:
        return None


# ── Directory operations ─────────────────────────────────────────────


async def makedirs(path: PathLike, mode: int = 0o755, exist_ok: bool = True) -> bool:
    """Create ``path`` (and missing parents). Return True on success."""
    try:
        await asyncio.to_thread(os.makedirs, str(path), mode, exist_ok)
        return True
    except OSError as e:
        logger.warning("makedirs(%s): %s", path, e)
        return False


async def ensure_dir(path: PathLike) -> bool:
    """Thin alias for ``makedirs(path, exist_ok=True)``."""
    return await makedirs(path, exist_ok=True)


# ── Copy / move / remove ─────────────────────────────────────────────


async def copy(src: PathLike, dst: PathLike) -> bool:
    """Copy ``src`` → ``dst`` preserving metadata. Return True on success."""
    try:
        await asyncio.to_thread(shutil.copy2, str(src), str(dst))
        return True
    except OSError as e:
        logger.warning("copy(%s, %s): %s", src, dst, e)
        return False


async def move(src: PathLike, dst: PathLike) -> bool:
    """Move ``src`` → ``dst``. Return True on success."""
    try:
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return True
    except OSError as e:
        logger.warning("move(%s, %s): %s", src, dst, e)
        return False


async def remove(path: PathLike) -> bool:
    """Delete a file or directory tree at ``path``. Return True on success."""
    def _remove_sync() -> bool:
        p = str(path)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
        return True

    try:
        return await asyncio.to_thread(_remove_sync)
    except OSError as e:
        logger.warning("remove(%s): %s", path, e)
        return False


# ── Text read / write ────────────────────────────────────────────────


async def read_text(path: PathLike, encoding: str = "utf-8") -> str | None:
    """Read text file. Return None on any OSError or decode failure."""
    def _read_sync() -> str | None:
        try:
            with open(str(path), "r", encoding=encoding) as f:
                return f.read()
        except Exception:
            return None

    return await asyncio.to_thread(_read_sync)


async def write_text(
    path: PathLike,
    content: str,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> bool:
    """Atomic write via tmp + rename. ``mode`` applies chmod after write."""
    return await asyncio.to_thread(_write_text_sync, path, content, encoding, mode)


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
    p = str(path)
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.rename(tmp, p)
        return True
    except Exception as e:
        logger.warning("write_text(%s): %s", path, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


# ── Bytes read / write ───────────────────────────────────────────────


async def write_bytes(path: PathLike, data: bytes) -> bool:
    """Atomic bytes write via tmp + rename. Return True on success."""
    return await asyncio.to_thread(_write_bytes_sync, path, data)


def _write_bytes_sync(path: PathLike, data: bytes) -> bool:
    """Sync implementation of ``write_bytes``; invoked via to_thread."""
    p = str(path)
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, p)
        return True
    except Exception as e:
        logger.warning("write_bytes(%s): %s", path, e)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


# ── JSON read / write ────────────────────────────────────────────────


async def read_json(path: PathLike) -> dict[str, Any]:
    """Read + parse JSON. Return empty dict on any failure (missing,
    malformed, unreadable) — callers can't distinguish these cases,
    so wrap with explicit try/except if that matters.
    """
    return await asyncio.to_thread(_read_json_sync, path)


def _read_json_sync(path: PathLike) -> dict[str, Any]:
    """Sync implementation of ``read_json``; invoked via to_thread."""
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def write_json(
    path: PathLike,
    data: dict[str, Any],
    indent: int = 2,
    mode: int | None = None,
) -> bool:
    """Serialize ``data`` and atomic-write as JSON. Return True on success."""
    return await asyncio.to_thread(_write_json_sync, path, data, indent, mode)


def _write_json_sync(
    path: PathLike,
    data: dict[str, Any],
    indent: int,
    mode: int | None,
) -> bool:
    """Sync implementation of ``write_json``; invoked via to_thread."""
    try:
        content = json.dumps(data, indent=indent, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning("write_json(%s): serialization failed: %s", path, e)
        return False
    return _write_text_sync(path, content + "\n", "utf-8", mode)
