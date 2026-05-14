"""Filesystem primitives shared by install/uninstall.

OP-51b | py_modules/unifideck/stores/gog/install/primitives.py

``GOGFolderOps`` is a stateless helper class exposing static methods
for the recurring filesystem operations of the install pipeline:

* ``folder_size(path)`` — sum bytes recursively;
* ``count_files(path)`` — count regular files recursively;
* ``has_goggame_info(path, game_id)`` — check for the GOG marker;
* ``force_cleanup_folder(path)`` — best-effort recursive removal
  (async, runs inside ``asyncio.to_thread``).

Errors on individual files are tolerated (broken symlinks, permission
denied) so a single problematic entry doesn't abort the whole sweep.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


class GOGFolderOps:
    """Gogfolder ops."""

    @staticmethod
    def folder_size(path: str) -> int:
        """Folder size."""
        total = 0
        try:
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += os.path.getsize(
                            os.path.join(root, name),
                        )
                    except OSError:
                        continue
        except OSError:
            pass
        return total

    @staticmethod
    def count_files(path: str) -> int:
        """Count files."""
        count = 0
        try:
            for _root, _dirs, files in os.walk(path):
                count += len(files)
        except OSError:
            pass
        return count

    @staticmethod
    def has_goggame_info(path: str, game_id: str = "") -> bool:
        """Check whether goggame info."""
        try:
            for name in os.listdir(path):
                if not name.startswith("goggame-"):
                    continue
                if not name.endswith(".info"):
                    continue
                if not game_id:
                    return True
                if name == f"goggame-{game_id}.info":
                    return True
        except OSError:
            pass
        return False

    @staticmethod
    async def force_cleanup_folder(path: str) -> None:
        """Force cleanup folder."""

        def _sync_cleanup() -> None:
            """Sync cleanup."""
            deleted = 0
            errors = 0
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    try:
                        os.remove(os.path.join(root, name))
                        deleted += 1
                    except OSError as e:
                        logger.debug(
                            "[GOGFolderOps] could not remove %s: %s",
                            name,
                            e,
                        )
                        errors += 1
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except OSError:
                        pass
            try:
                os.rmdir(path)
            except OSError:
                pass
            logger.info(
                "[GOGFolderOps] force cleanup: %d deleted, %d errors",
                deleted,
                errors,
            )

        await asyncio.to_thread(_sync_cleanup)
