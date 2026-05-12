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
    """Walk mtimes."""
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
    """Copy tree."""
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
    """Read text."""
    return Path(path).read_text(encoding="utf-8")


def write_text(path: str, content: str) -> None:
    """Write text."""
    Path(path).write_text(content, encoding="utf-8")
