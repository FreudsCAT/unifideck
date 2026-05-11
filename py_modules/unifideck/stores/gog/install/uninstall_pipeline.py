"""uninstall_pipeline.py — Best-effort uninstall of a GOG install.

# OP-51e | py_modules/unifideck/stores/gog/install/uninstall_pipeline.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import TYPE_CHECKING

from ....core.types import Result
from .primitives import GOGFolderOps

if TYPE_CHECKING:
    from .installer import GOGInstaller

logger = logging.getLogger(__name__)
_UNINSTALL_MAX_ATTEMPTS = 3


class _UninstallPipeline:
    """Uninstall pipeline."""

    def __init__(self, parent: GOGInstaller) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def uninstall_game(
        self, game_id: str, install_path: str | None = None,
    ) -> Result:
        """Uninstall game."""
        if not install_path:
            return Result(success=False, error='install_path_required')
        if not os.path.isdir(install_path):
            return Result(success=True, data={'already_gone': True})
        for attempt in range(1, _UNINSTALL_MAX_ATTEMPTS + 1):
            try:
                await asyncio.to_thread(
                    shutil.rmtree, install_path,
                )
            except OSError as e:
                logger.warning(
                    '[GOGUninstall] attempt %d failed: %s', attempt, e,
                )
                await asyncio.sleep(0.5)
                continue
            if not os.path.isdir(install_path):
                break
        if os.path.isdir(install_path):
            await GOGFolderOps.force_cleanup_folder(install_path)
        if os.path.isdir(install_path):
            return Result(success=False, error='deletion_failed')
        return Result(
            success=True, data={'game_id': game_id, 'path': install_path},
        )
