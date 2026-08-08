"""Installed-game on-disk size — one process for every store.

Measuring the exact "Installed size" is the SAME for every store:
locate the install directory, then sum the bytes under it. Only the
*source* of the path differs per store, and that's exposed uniformly
through :meth:`StoreBase.get_installed_path`. Everything else — the
path fallback and the directory walk — lives here, once, so the RPC
layer and every store share a single implementation.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any


def dir_size_bytes(path: str) -> int:
    """Sum the on-disk byte size of every regular file under ``path``.

    Iterative ``os.scandir`` walk (reuses each ``DirEntry``'s cached
    stat, so it's cheaper than ``os.walk`` + ``os.stat`` on large
    install trees). Symlinks are not followed — avoids cycles and
    double-counting. Unreadable entries are skipped: this only feeds a
    size display, so a partial total beats an error.
    """
    total = 0
    stack = [path]
    while stack:
        subdirs, size = _scan_one_dir(stack.pop())
        stack.extend(subdirs)
        total += size
    return total


def _scan_one_dir(current: str) -> tuple[list[str], int]:
    """Return ``(subdir paths, summed file bytes)`` for one directory level."""
    subdirs: list[str] = []
    total = 0
    try:
        with os.scandir(current) as it:
            for entry in it:
                total += _entry_size(entry, subdirs)
    except OSError:
        return subdirs, total
    return subdirs, total


def _entry_size(entry: os.DirEntry[str], subdirs: list[str]) -> int:
    """Queue directories onto *subdirs*; return a file's byte size (else 0)."""
    try:
        if entry.is_dir(follow_symlinks=False):
            subdirs.append(entry.path)
            return 0
        if entry.is_file(follow_symlinks=False):
            return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0
    return 0


async def resolve_installed_dir(
    adapter: Any, cache_path: Any, game_id: Any,
) -> str | None:
    """Locate an installed game's directory, or ``None`` if it's gone.

    Resolution order:

    1. the sync cache's ``install_path`` (when its dir still exists);
    2. the store's own install records via
       :meth:`StoreBase.get_installed_path` — the cache is unreliable
       (e.g. sync-detected Epic installs land with ``install_path =
       None``, and a moved/removed game leaves a stale path).

    Shared with the QAM "Installed" list
    (:mod:`unifideck.services.installed_disk_info`), which labels each
    game internal/external: it must classify the SAME directory this
    function sizes, or a game whose cached path is stale would be sized
    from its real install dir and labelled from the dead one.
    """
    path = cache_path if isinstance(cache_path, str) and cache_path else None
    if path is not None and await asyncio.to_thread(os.path.isdir, path):
        return path
    if adapter is not None and game_id:
        with contextlib.suppress(Exception):
            resolved = await adapter.get_installed_path(game_id)
            if (
                isinstance(resolved, str) and resolved
                and await asyncio.to_thread(os.path.isdir, resolved)
            ):
                return resolved
    return None


async def installed_size_bytes(
    adapter: Any, cache_path: Any, game_id: Any,
) -> int:
    """Exact on-disk size (bytes) of an installed game's directory.

    Resolves the directory via :func:`resolve_installed_dir`, then walks
    it off the event loop.

    Returns ``0`` ("—") rather than ever falling back to a download
    size: an installed game must never display its (smaller) download
    size as the installed size.
    """
    path = await resolve_installed_dir(adapter, cache_path, game_id)
    if path is None:
        return 0
    try:
        return await asyncio.to_thread(dir_size_bytes, path)
    except OSError:
        return 0
