"""Cloud save manifest — per-game list of save files.

OP-17c | py_modules/unifideck/services/cloud_save/manifest.py

A "manifest" is the list of save files (paths + last-known mtimes)
for a given game. The manifest is stored alongside the cloud cache
and updated on every successful sync. Three functions :

* ``read_manifest(game_id, paths)`` — load from disk;
* ``write_manifest(manifest, paths)`` — atomic save;
* ``build_manifest(local_dir)`` — walk a save directory and produce
  a fresh manifest snapshot.
"""

from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import cast
from .constants import MANIFEST_FILE
from .fs_ops import read_text, walk_mtimes, write_text

logger = logging.getLogger(__name__)


async def read_manifest(directory: str) -> dict[str, float]:
    """Load the saved-manifest file from a directory.

    Reads ``<directory>/MANIFEST_FILE`` (typically
    ``.unifideck-cloud-manifest.json``) and parses it. Returns an
    empty dict when the file is absent or malformed — never raises.
    The caller treats an empty manifest as "no prior sync state".

    Args:
        directory: absolute path of the save directory (local or
            remote — both sides keep a manifest).

    Returns:
        Mapping ``"relative/path/to/file" → posix_mtime``.
        Empty dict on missing or corrupted manifest.
    """
    path = str(Path(directory) / MANIFEST_FILE)
    if not await asyncio.to_thread(
        lambda: Path(path).is_file(),
    ):
        return {}
    try:
        raw = await asyncio.to_thread(read_text, path)
        return cast("dict[str, float]", json.loads(raw))
    except (OSError, json.JSONDecodeError):
        return {}


async def write_manifest(directory: str, manifest: dict[str, float]) -> None:
    """Atomically persist the manifest to disk.

    Writes the JSON-serialised manifest to ``<file>.tmp`` then
    renames it over the target path. The rename is atomic on every
    supported filesystem, so a crash mid-write can't leave a
    partial manifest visible.

    On I/O errors (disk full, permission denied, etc.) the write is
    silently dropped and logged at WARN — a missing manifest is
    recoverable (next sync rebuilds it), so a hard failure here
    would be more harmful than the missed write.

    Args:
        directory: directory to write the manifest into.
        manifest: mapping returned by ``build_manifest`` or merged
            from prior syncs.
    """
    path = str(Path(directory) / MANIFEST_FILE)
    tmp = f"{path}.tmp"
    try:
        await asyncio.to_thread(
            write_text,
            tmp,
            json.dumps(manifest),
        )
        await asyncio.to_thread(
            lambda: Path(tmp).replace(path),
        )
    except OSError as e:
        logger.warning(
            "[CloudSaveService] manifest write failed: %s",
            e,
        )


async def build_manifest(directory: str) -> dict[str, float]:
    """Snapshot a directory's contents into a fresh manifest.

    Walks the directory recursively (via ``walk_mtimes``) and
    captures every regular file's mtime. The result is keyed by
    path **relative to** ``directory`` so manifests can be compared
    cross-host without dragging absolute paths into the comparison.

    Args:
        directory: directory to snapshot.

    Returns:
        Fresh mapping ``"relative/path" → posix_mtime``. Empty
        dict if the directory doesn't exist or is empty.
    """
    if not await asyncio.to_thread(
        lambda: Path(directory).is_dir(),
    ):
        return {}
    return await asyncio.to_thread(walk_mtimes, directory)
