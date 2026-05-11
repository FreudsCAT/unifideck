"""primitives.py — Folder-level helpers shared across the install pipeline.

# OP-51b | py_modules/unifideck/stores/gog/install/primitives.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


class GOGFolderOps:
    """GOG folder ops."""

    @staticmethod
    def folder_size(path: str) -> int:
        """Folder size."""
        if not os.path.isdir(path):
            return 0
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
        return total

    @staticmethod
    def count_files(path: str) -> int:
        """Count files."""
        if not os.path.isdir(path):
            return 0
        total = 0
        for _root, _dirs, files in os.walk(path):
            total += len(files)
        return total

    @staticmethod
    def has_goggame_info(path: str, game_id: str = '') -> bool:
        """Has goggame info."""
        if not os.path.isdir(path):
            return False
        if game_id:
            wanted = f'goggame-{game_id}.info'
            try:
                if os.path.isfile(os.path.join(path, wanted)):
                    return True
            except OSError:
                pass
        try:
            for name in os.listdir(path):
                if name.startswith('goggame-') and name.endswith('.info'):
                    return True
        except OSError:
            return False
        return False

    @staticmethod
    async def force_cleanup_folder(path: str) -> None:
        """Force cleanup folder."""
        if not path or not os.path.isdir(path):
            return
        import shutil

        async def _rmtree() -> None:
            await asyncio.to_thread(
                shutil.rmtree, path, ignore_errors=True,
            )

        for _attempt in range(3):
            await _rmtree()
            if not os.path.isdir(path):
                return
            await asyncio.sleep(0.5)
