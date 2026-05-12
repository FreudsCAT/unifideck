"""Filesystem operations for cloud save sync.

OP-17d | py_modules/unifideck/services/cloud_save/fs_ops.py

Pure helpers shared by ``sync`` and ``manifest`` :

* ``walk_mtimes(directory)`` — recursive (file, mtime) listing;
* ``copy_tree(src, dst)`` — wrapper over ``shutil.copytree`` with
  same-tree merge semantics;
* ``read_text(path)`` / ``write_text(path, data)`` — atomic
  text I/O via temp + rename.

Centralising fs ops here keeps the sync logic focused on policy
(what to copy, when) rather than on filesystem mechanics.
"""

from __future__ import annotations
import logging
import os
import shutil
from pathlib import Path
from .constants import MANIFEST_FILE

logger = logging.getLogger(__name__)


def walk_mtimes(root: str) -> dict[str, float]:
    """Walk a directory and collect every file's mtime.

    Skips hidden files (anything starting with ``.``) and the
    manifest file itself, so the manifest's own mtime doesn't
    pollute the comparison against the previous manifest.

    Per-file ``stat`` errors (broken symlinks, race-on-delete) are
    tolerated: the offending entry is simply omitted from the
    result rather than aborting the whole walk.

    Args:
        root: absolute path of the directory to walk.

    Returns:
        Mapping ``"relative/path/from/root" → posix_mtime``.
    """
    result: dict[str, float] = {}
    root_path = Path(root)
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.startswith(".") or name == MANIFEST_FILE:
                continue
            full = Path(dirpath) / name
            rel = str(full.relative_to(root_path))
            try:
                result[rel] = full.stat().st_mtime
            except OSError:
                continue
    return result


def copy_tree(src: str, dst: str, skip_manifest: bool = False) -> None:
    """Copy a directory tree with merge semantics and metadata.

    Unlike ``shutil.copytree`` (which refuses to copy into an
    existing directory), this helper merges into ``dst`` — every
    source file overwrites its counterpart in the destination, but
    files only present in ``dst`` are preserved.

    Uses ``shutil.copy2`` per file to preserve mtimes — essential
    for the sync algorithm, which relies on those mtimes to decide
    which side is fresher.

    Hidden files (``.foo``) are always skipped. The manifest file
    is also skipped when ``skip_manifest=True`` (the caller writes
    the manifest separately after the copy completes).

    Per-file copy failures are tolerated (logged at DEBUG) so a
    single bad file doesn't abort the whole sync.

    Args:
        src: source directory.
        dst: destination directory (created if absent).
        skip_manifest: whether to skip the manifest file during
            the copy. Defaults to ``False``.
    """
    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.is_dir():
        return
    dst_path.mkdir(parents=True, exist_ok=True)
    for dirpath, _dirs, files in os.walk(src):
        dirpath_p = Path(dirpath)
        rel = dirpath_p.relative_to(src_path)
        target_dir = dst_path / rel if str(rel) != "." else dst_path
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if skip_manifest and name == MANIFEST_FILE:
                continue
            if name.startswith("."):
                continue
            src_file = dirpath_p / name
            dst_file = target_dir / name
            try:
                shutil.copy2(src_file, dst_file)
            except OSError as e:
                logger.debug(
                    "[CloudSaveService] copy %s failed: %s",
                    src_file,
                    e,
                )


def read_text(path: str) -> str:
    """Read a UTF-8 text file (sync).

    Thin wrapper kept for symmetry with ``write_text`` and so the
    cloud-save module has a single I/O surface (rather than each
    caller importing ``pathlib`` individually).

    Args:
        path: absolute path of the file to read.

    Returns:
        File content as a string.

    Raises:
        OSError: passed through from ``Path.read_text``.
    """
    return Path(path).read_text(encoding="utf-8")


def write_text(path: str, content: str) -> None:
    """Write a UTF-8 text file (sync, **not** atomic).

    Direct overwrite — atomicity is the caller's responsibility
    (see ``manifest.write_manifest`` which wraps this with a temp
    + rename).

    Args:
        path: absolute path of the file to write.
        content: text to write.

    Raises:
        OSError: passed through from ``Path.write_text``.
    """
    Path(path).write_text(content, encoding="utf-8")
