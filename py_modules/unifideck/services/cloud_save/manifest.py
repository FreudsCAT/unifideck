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
    """Read manifest."""
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
    """Write manifest."""
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
    """Build manifest."""
    if not await asyncio.to_thread(
        lambda: Path(directory).is_dir(),
    ):
        return {}
    return await asyncio.to_thread(walk_mtimes, directory)
