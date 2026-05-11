"""planner.py — Decide whether to install fresh, resume, or repair.

# OP-51d | py_modules/unifideck/stores/gog/install/planner.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import GOGConfig
from ..tokens import GOGTokenManager
from .primitives import GOGFolderOps

logger = logging.getLogger(__name__)
_CORRUPT_INSTALL_SIZE_THRESHOLD = 100 * 1024 * 1024
_MIN_SIZE_RATIO = 0.8


def _extract_disk_size_from_size_info(size_info: dict) -> int | None:
    """Extract disk size from size info."""
    if not isinstance(size_info, dict):
        return None
    for key in ('disk_size', 'size_on_disk', 'size'):
        value = size_info.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


class GOGInstallPlanner:
    """GOG install planner."""

    def __init__(
        self, config: GOGConfig, tokens: GOGTokenManager,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._gogdl_bin: str | None = None

    async def determine_install_mode(
        self, game_id: str, target_folder: str | None,
    ) -> str:
        """Determine install mode."""
        if not target_folder or not Path(target_folder).is_dir():
            return 'download'
        if GOGFolderOps.has_goggame_info(target_folder, game_id):
            size = GOGFolderOps.folder_size(target_folder)
            if size < _CORRUPT_INSTALL_SIZE_THRESHOLD:
                await self._cleanup_corrupt_install(game_id, target_folder)
                return 'download'
            return 'repair'
        await self._cleanup_orphaned_install(game_id, target_folder)
        return 'download'

    async def verify_installation(
        self,
        game_id: str,
        install_path: str,
        platform: str,
        exe_finder: Callable[[str], str | None],
    ) -> dict[str, Any]:
        """Verify installation."""
        size_on_disk = GOGFolderOps.folder_size(install_path)
        file_count = GOGFolderOps.count_files(install_path)
        expected = await self.get_expected_disk_size(game_id, platform)
        executable = exe_finder(install_path) if exe_finder else None
        ok = (
            size_on_disk > 0
            and (expected == 0 or size_on_disk >= expected * _MIN_SIZE_RATIO)
            and bool(executable)
        )
        return {
            'success': ok,
            'size_on_disk': size_on_disk,
            'expected_size': expected,
            'file_count': file_count,
            'executable': executable or '',
        }

    async def get_expected_disk_size(
        self, game_id: str, platform: str,
    ) -> int:
        """Get expected disk size."""
        if not self._gogdl_bin:
            return 0
        stdout = await self._spawn_gogdl_info(
            self._gogdl_bin, game_id, platform,
        )
        if stdout is None:
            return 0
        return self._parse_size_from_gogdl_info(stdout)

    async def _spawn_gogdl_info(
        self, gogdl_bin: str, game_id: str, platform: str,
    ) -> bytes | None:
        """Spawn GOGDL info."""
        try:
            async with self._tokens.gogdl_credentials() as env:
                proc = await asyncio.create_subprocess_exec(
                    gogdl_bin, 'info', game_id, '--os', platform,
                    env={**env},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, _err = await asyncio.wait_for(
                        proc.communicate(), timeout=30.0,
                    )
                except TimeoutError:
                    return None
                return stdout if proc.returncode == 0 else None
        except OSError as e:
            logger.debug('[GOGPlanner] info spawn: %s', e)
            return None

    @staticmethod
    def _parse_size_from_gogdl_info(stdout: bytes) -> int:
        """Parse size from GOGDL info."""
        try:
            text = stdout.decode('utf-8', errors='replace')
        except UnicodeDecodeError:
            return 0
        for line in text.splitlines():
            if not line.strip().startswith('{'):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            size = _extract_disk_size_from_size_info(
                data if isinstance(data, dict) else {},
            )
            if size:
                return size
        return 0

    async def _cleanup_corrupt_install(
        self, game_id: str, target_folder: str,
    ) -> None:
        """Cleanup corrupt install."""
        logger.info(
            '[GOGPlanner] corrupt install for %s — wiping %s',
            game_id, target_folder,
        )
        await asyncio.to_thread(
            shutil.rmtree, target_folder, ignore_errors=True,
        )

    async def _cleanup_orphaned_install(
        self, game_id: str, target_folder: str,
    ) -> None:
        """Cleanup orphaned install."""
        # When the folder exists but lacks goggame info we treat it
        # as orphaned: drop it so the install can claim the path.
        logger.info(
            '[GOGPlanner] orphaned install %s — wiping %s',
            game_id, target_folder,
        )
        await asyncio.to_thread(
            shutil.rmtree, target_folder, ignore_errors=True,
        )

    def manifest_locations(self, game_id: str) -> list[str]:
        """Manifest locations."""
        config_dir = Path(self._config.gogdl_config_dir).expanduser()
        return [
            str(config_dir / 'manifests' / game_id),
            str(config_dir / f'{game_id}.manifest'),
        ]

    def _resolve_gogdl_bin(self) -> str | None:
        """Resolve GOGDL bin."""
        return self._gogdl_bin

    def set_gogdl_bin(self, path: str) -> None:
        """Set GOGDL bin."""
        self._gogdl_bin = path
