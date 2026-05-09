"""services/cloud_save/fs_ops.py — Filesystem primitives for cloud save sync.

Pure sync functions — the service runs them via
``asyncio.to_thread`` to avoid blocking the event loop. Kept
separate so ``service.py`` stays focused on orchestration
(manifest compare, conflict routing) rather than I/O mechanics.
"""
from __future__ import annotations

import logging
import os
import shutil

from .constants import MANIFEST_FILE

logger = logging.getLogger(__name__)


def walk_mtimes(root: str) -> dict[str, float]:
    """Return a flat ``{relpath: mtime}`` map for files under ``root``.

    Skips dot-files and the manifest itself. Per-file OSError
    (file vanished mid-walk) is silently skipped — the caller
    gets a partial map which is still useful for diff.
    """
    mtimes = {}
    if not os.path.isdir(root):
        return mtimes

    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.startswith(".") or f == MANIFEST_FILE:
                continue

            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root)
            try:
                mtimes[rel] = os.path.getmtime(path)
            except OSError:
                pass

    return mtimes


def copy_tree(
    src: str,
    dst: str,
    skip_manifest: bool = False,
) -> None:
    """Recursively copy ``src`` → ``dst`` preserving mtimes.

    Unlike ``shutil.copytree``, merges into an existing
    directory instead of failing. ``skip_manifest=True``
    excludes the manifest file — callers refresh it separately
    via ``write_manifest`` so the old manifest never gets
    copied forward with stale mtimes. Skips dot-files. Per-file
    OSError logged at DEBUG, copy continues.
    """
    if not os.path.isdir(src):
        return

    os.makedirs(dst, exist_ok=True)

    for dirpath, dirnames, files in os.walk(src):
        rel_dir = os.path.relpath(dirpath, src)
        dst_dir = os.path.join(dst, rel_dir) if rel_dir != "." else dst

        os.makedirs(dst_dir, exist_ok=True)

        for f in files:
            if f.startswith("."):
                # Handle manifest skipping explicitly if it's dot-prefixed
                if skip_manifest and f == MANIFEST_FILE:
                    continue
                # The spec says "skips dot-files". But saves might use dot files?
                # We will follow the spec "Skips dot-files".
                continue

            src_file = os.path.join(dirpath, f)
            dst_file = os.path.join(dst_dir, f)

            try:
                shutil.copy2(src_file, dst_file)
            except OSError as e:
                logger.debug("[CloudSaveFsOps] failed to copy %s: %s", src_file, e)


def read_text(path: str) -> str:
    """Read ``path`` as UTF-8 text. Raises OSError on missing file."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path: str, content: str) -> None:
    """Write ``content`` to ``path`` as UTF-8 text (overwrite)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
        
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
