"""core/io/async_file_ops.py — Non-blocking I/O wrapper.

Moved from core/ to the new core/io/ subpackage,
paired with safe_file_op.py under a single "filesystem I/O
primitives" umbrella. Clean break: no shim in core/.

Wraps all synchronous file operations (open, os.path.exists(),
os.makedirs, shutil.copy) in asyncio.to_thread() to avoid blocking
the single-threaded asyncio event loop Decky Loader runs on.
Replaces 39+ blocking I/O calls found in main.py inside async def
methods. Each blocking call froze the Steam UI for the duration of
the disk access (1-10ms per call on eMMC).
All functions in this module are standalone `async def` coroutines
that accept the same arguments as their sync counterparts and return
the same values. The only rule: `await` them from an async context.
Features:
- JSON read/write helpers with corrupt-file recovery.
- Atomic write via tmp + rename with optional chmod (SEC-21b).
- Directory helpers (exists, is_file, is_dir, listdir, makedirs).
- Copy / move / remove helpers.
Usage:
 from unifideck.core import async_file_ops as aio
 data = await aio.read_json("/path/to/cache.json")
 await aio.write_json("/path/to/cache.json", data, mode=0o600)
Reference: Technical Document v1.0 — Section 3.8 (Async I/O migration),
Figure 29.
"""
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)
PathLike = str | os.PathLike
# ══════════════════════════════════════════════════════════════════
# Path queries
# ══════════════════════════════════════════════════════════════════


async def exists(path: PathLike) -> bool:
    """Check if a path exists (file or directory)."""
    return await asyncio.to_thread(
        lambda: Path(path).exists(),
    )


async def is_file(path: PathLike) -> bool:
    """Check if a path is a regular file."""
    return await asyncio.to_thread(
        lambda: Path(path).is_file(),
    )


async def is_dir(path: PathLike) -> bool:
    """Check if a path is a directory."""
    return await asyncio.to_thread(
        lambda: Path(path).is_dir(),
    )


async def listdir(path: PathLike) -> list[str]:
    """List entries in a directory. Returns [] on error."""
    try:
        return await asyncio.to_thread(
            lambda: [p.name for p in Path(path).iterdir()],
        )
    except OSError as e:
        logger.warning(
            "[AsyncFileOps] listdir(%s) failed: %s", path, e,
        )
        return []


async def stat(path: PathLike) -> os.stat_result | None:
    """Return os.stat_result for path or None on error."""
    try:
        return await asyncio.to_thread(
            lambda: Path(path).stat(),
        )
    except OSError:
        return None


# ══════════════════════════════════════════════════════════════
# Directory management
# ══════════════════════════════════════════════════════════════


async def makedirs(path: PathLike, mode: int = 0o755,
                   exist_ok: bool = True) -> bool:
    """Create directory tree. Returns True on success."""
    try:
        await asyncio.to_thread(
            lambda: Path(path).mkdir(
                mode=mode, parents=True, exist_ok=exist_ok,
            ),
        )
        return True
    except OSError as e:
        logger.error(
            "[AsyncFileOps] makedirs(%s) failed: %s", path, e,
        )
        return False


async def ensure_dir(path: PathLike) -> bool:
    """Alias for makedirs(..., exist_ok=True)."""
    return await makedirs(path, exist_ok=True)


# ══════════════════════════════════════════════════════════════
# File copy / move / remove
# ══════════════════════════════════════════════════════════════


async def copy(src: PathLike, dst: PathLike) -> bool:
    """Copy a file. Returns True on success."""
    try:
        await asyncio.to_thread(shutil.copy2, src, dst)
        return True
    except (OSError, shutil.Error) as e:
        logger.error(
            "[AsyncFileOps] copy(%s -> %s) failed: %s",
            src, dst, e,
        )
        return False


async def move(src: PathLike, dst: PathLike) -> bool:
    """Move a file or directory. Returns True on success."""
    try:
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return True
    except (OSError, shutil.Error) as e:
        logger.error(
            "[AsyncFileOps] move(%s -> %s) failed: %s",
            src, dst, e,
        )
        return False


async def remove(path: PathLike) -> bool:
    """Delete a file or directory tree. Returns True on success."""
    try:
        def _remove_sync():
            p = Path(path)
            if not p.exists():
                return
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)

        await asyncio.to_thread(_remove_sync)
        return True
    except OSError as e:
        logger.error(
            "[AsyncFileOps] remove(%s) failed: %s", path, e,
        )
        return False


# ══════════════════════════════════════════════════════════════
# Text I/O
# ══════════════════════════════════════════════════════════════


async def read_text(path: PathLike, encoding: str = "utf-8") -> str | None:
    """Read file contents as text. Returns None on error."""
    try:
        return await asyncio.to_thread(
            lambda: Path(path).read_text(encoding=encoding)
        )
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("[AsyncFileOps] read_text(%s) failed: %s", path, e)
        return None


async def write_text(path: PathLike, content: str,
                     encoding: str = "utf-8", mode: int = 0o644) -> bool:
    """Write text atomically (tmp + rename) with optional chmod."""
    return await asyncio.to_thread(_write_text_sync, path, content, encoding, mode)


def _write_text_sync(path: PathLike, content: str,
                     encoding: str, mode: int) -> bool:
    """Synchronous helper for write_text, called via to_thread."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    try:
        tmp.write_text(content, encoding=encoding)
        tmp.replace(p)
        if mode != 0o644:
            p.chmod(mode)
        return True
    except OSError as e:
        logger.error(
            "[AsyncFileOps] write_text(%s) failed: %s", path, e,
        )
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                # best-effort cleanup; file may already be gone or locked
                pass
        return False


async def write_bytes(path: PathLike, data: bytes) -> bool:
    """Atomic binary-file write. Uses temp-file-and-rename.

    Same semantics as ``write_text`` but for ``bytes`` payloads.
    Used for artwork downloads (PNG/JPEG) and any other blob
    content. Returns True on success, False on any I/O failure
    (logs via the caller — this helper stays silent).
    """
    return await asyncio.to_thread(_write_bytes_sync, path, data)


def _write_bytes_sync(path: PathLike, data: bytes) -> bool:
    """Synchronous binary writer called via to_thread."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        tmp.replace(p)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            # best-effort operation; failure is non-fatal here
            pass
        return False


# ══════════════════════════════════════════════════════════════════
# JSON I/O
# ══════════════════════════════════════════════════════════════════


async def read_json(path: PathLike) -> dict[str, Any]:
    """Read a JSON file. Returns {} on missing file or parse error."""
    return await asyncio.to_thread(_read_json_sync, path)


def _read_json_sync(path: PathLike) -> dict[str, Any]:
    """Synchronous JSON reader called via to_thread."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(
                    "[AsyncFileOps] JSON %s is not a dict; returning {}",
                    path,
                )
                return {}
            return data
    except (
        json.JSONDecodeError, UnicodeDecodeError, OSError,
    ) as e:
        logger.warning(
            "[AsyncFileOps] failed to read JSON %s: %s", path, e,
        )
        return {}


async def write_json(path: PathLike, data: Any, indent: int = 2,
                     mode: int = 0o644) -> bool:
    """Write JSON atomically (SEC-21b: tmp + rename + chmod).

    Args:
    path: Destination file path.
    data: JSON-serializable object.
    indent: Pretty-print indent (default 2).
    mode: Unix file mode applied after rename (default 0o644;
    use 0o600 for files containing secrets).

    Returns:
    True on success, False on error.

    """
    return await asyncio.to_thread(_write_json_sync, path, data, indent, mode)


def _write_json_sync(path: PathLike, data: Any, indent: int,
                     mode: int = 0o644) -> bool:
    """Write JSON atomically via tmp + rename, with optional chmod."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    try:
        content = json.dumps(
            data, ensure_ascii=False, indent=indent,
        )
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)
        if mode != 0o644:
            p.chmod(mode)
        return True
    except (OSError, TypeError, ValueError) as e:
        logger.error(
            "[AsyncFileOps] write_json(%s) failed: %s", path, e,
        )
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                # best-effort cleanup; file may already be gone or locked
                pass
        return False
