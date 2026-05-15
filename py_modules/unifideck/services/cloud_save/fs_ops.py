"""services/cloud_save/fs_ops.py — Filesystem primitives for cloud save sync.

Pure sync functions — the service runs them via
``asyncio.to_thread`` to avoid blocking the event loop. Kept
separate so ``service.py`` stays focused on orchestration
(manifest compare, conflict routing) rather than I/O mechanics.

Refactor history (2026-05-14): ``copy_tree`` was a single
function at CC=17. The inner ``for file in files`` body had
three stacked ``if`` branches (dot-file check, manifest check,
copy with try/except) which all added to the parent nesting
score. Pulled the skip decision and the per-file copy out into
helpers so the outer loop is a flat read.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
from pathlib import Path

from .constants import MANIFEST_FILE

logger = logging.getLogger(__name__)


def walk_mtimes(root: str) -> dict[str, float]:
    """Return a flat ``{relpath: mtime}`` map for files under ``root``.

    Skips dot-files and the manifest itself. Per-file OSError
    (file vanished mid-walk) is silently skipped — the caller
    gets a partial map which is still useful for diff.
    """
    mtimes: dict[str, float] = {}
    if not Path(root).is_dir():
        return mtimes

    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.startswith(".") or f == MANIFEST_FILE:
                continue

            path = str(Path(dirpath) / f)
            rel = os.path.relpath(path, root)
            with contextlib.suppress(OSError):
                mtimes[rel] = Path(path).stat().st_mtime

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
    if not Path(src).is_dir():
        return

    Path(dst).mkdir(parents=True, exist_ok=True)

    for dirpath, _dirnames, files in os.walk(src):
        rel_dir = os.path.relpath(dirpath, src)
        dst_dir = str(Path(dst) / rel_dir) if rel_dir != "." else dst
        Path(dst_dir).mkdir(parents=True, exist_ok=True)
        for f in files:
            if _should_skip_file(f, skip_manifest):
                continue
            _copy_one_file(dirpath, dst_dir, f)


# ─────────────────────────────────────────────────────────────────
# Private helpers — extracted from a former single CC=17 function
# ─────────────────────────────────────────────────────────────────


def _should_skip_file(name: str, skip_manifest: bool) -> bool:
    """Decide whether a file name should be skipped by ``copy_tree``.

    The spec is "always skip dot-files". The ``skip_manifest``
    flag adds the manifest to the skip set (manifest is
    dot-prefixed in current layout, so it's *already* skipped
    by the dot-rule — the explicit branch is kept for a future
    where the manifest file name changes; right now the second
    ``return`` is the always-taken path for dot-prefixed names).
    """
    if name.startswith("."):
        return True
    return bool(skip_manifest and name == MANIFEST_FILE)


def _copy_one_file(src_dir: str, dst_dir: str, name: str) -> None:
    """Copy one file from ``src_dir`` into ``dst_dir`` preserving mtime.

    Uses ``shutil.copy2`` which preserves metadata (mtime,
    permissions) — mtime preservation is what makes the next
    ``walk_mtimes`` comparison reliable. Per-file OSError is
    swallowed at DEBUG : a cloud save with a few unreadable
    files is still worth syncing for the rest.
    """
    src_file = str(Path(src_dir) / name)
    dst_file = str(Path(dst_dir) / name)
    try:
        shutil.copy2(src_file, dst_file)
    except OSError as err:
        logger.debug(
            "[CloudSaveFsOps] failed to copy %s: %s", src_file, err,
        )


def read_text(path: str) -> str:
    """Read ``path`` as UTF-8 text. Raises OSError on missing file."""
    with Path(path).open(encoding="utf-8") as f:
        return f.read()


def write_text(path: str, content: str) -> None:
    """Write ``content`` to ``path`` as UTF-8 text (overwrite)."""
    parent = str(Path(path).parent)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)

    with Path(path).open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
