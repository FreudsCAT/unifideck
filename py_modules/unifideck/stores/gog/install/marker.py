"""marker.py — Post-install marker writer + manifest regeneration.

# OP-51g | py_modules/unifideck/stores/gog/install/marker.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import TYPE_CHECKING, Any, cast

from .primitives import GOGFolderOps

if TYPE_CHECKING:
    from .installer import GOGInstaller

logger = logging.getLogger(__name__)
_INSTALL_MARKER = '.unifideck-id'


class _PostInstallMarker:
    """Post install marker."""

    def __init__(self, parent: GOGInstaller) -> None:
        """Initialize the instance."""
        self._parent = parent

    @staticmethod
    def snapshot_dirs(base_path: str) -> set:
        """Snapshot dirs."""
        if not os.path.isdir(base_path):
            return set()
        try:
            return {
                name for name in os.listdir(base_path)
                if os.path.isdir(os.path.join(base_path, name))
            }
        except OSError:
            return set()

    async def locate_install(
        self,
        game_id: str,
        base_path: str,
        folder_name: str | None,
        existing_dirs: set,
    ) -> str | None:
        """Locate install."""
        if folder_name:
            candidate = os.path.join(base_path, folder_name)
            if GOGFolderOps.has_goggame_info(candidate, game_id):
                return candidate
        for name in self.snapshot_dirs(base_path) - existing_dirs:
            candidate = os.path.join(base_path, name)
            if GOGFolderOps.has_goggame_info(candidate, game_id):
                return candidate
        if self._find_flat_goggame(base_path, game_id):
            return await self._reorganise_flat_install(
                base_path, game_id, folder_name, existing_dirs,
            )
        return None

    @staticmethod
    def _find_flat_goggame(base_path: str, game_id: str) -> bool:
        """Find flat goggame."""
        try:
            for name in os.listdir(base_path):
                if name == f'goggame-{game_id}.info':
                    return True
        except OSError:
            return False
        return False

    @staticmethod
    async def _reorganise_flat_install(
        base_path: str,
        game_id: str,
        folder_name: str | None,
        existing_dirs: set,
    ) -> str:
        """Reorganise flat install."""
        target_name = folder_name or f'goggame-{game_id}'
        target = os.path.join(base_path, target_name)
        os.makedirs(target, exist_ok=True)

        def _move() -> None:
            try:
                for name in os.listdir(base_path):
                    if name == target_name or name in existing_dirs:
                        continue
                    src = os.path.join(base_path, name)
                    dst = os.path.join(target, name)
                    if src != target:
                        shutil.move(src, dst)
            except OSError as e:
                logger.warning('[GOGMarker] reorg failed: %s', e)

        await asyncio.to_thread(_move)
        return target

    async def write_install_marker(
        self, install_path: str, game_id: str, language: str,
    ) -> bool:
        """Write install marker."""
        if not install_path or not os.path.isdir(install_path):
            return False
        info_data = self._load_info_data_from_goggame(install_path, game_id)
        info_data.setdefault('game_id', game_id)
        info_data.setdefault('install_path', install_path)
        info_data['language'] = language
        marker = os.path.join(install_path, _INSTALL_MARKER)
        return await asyncio.to_thread(
            self._write_marker_sync, marker, info_data,
        )

    @staticmethod
    def _load_info_data_from_goggame(
        install_path: str, game_id: str,
    ) -> dict[str, Any]:
        """Load info data from goggame."""
        search_dirs = [install_path]
        for sub in ('game', 'bin'):
            sub_path = os.path.join(install_path, sub)
            if os.path.isdir(sub_path):
                search_dirs.append(sub_path)
        for directory in search_dirs:
            data = _PostInstallMarker._try_load_info_in_dir(directory, game_id)
            if data is not None:
                return data
        return {}

    @staticmethod
    def _try_load_info_in_dir(
        directory: str, game_id: str,
    ) -> dict[str, Any] | None:
        """Try load info in dir."""
        candidate = os.path.join(directory, f'goggame-{game_id}.info')
        if not os.path.isfile(candidate):
            return None
        try:
            with open(candidate, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return {
            'title': data.get('name') or '',
            'install_id': data.get('rootGameId') or data.get('gameId') or '',
            'build_id': str(data.get('buildId', '')),
        }

    @staticmethod
    def _write_marker_sync(
        marker_path: str, info_data: dict[str, Any],
    ) -> bool:
        """Write marker sync."""
        try:
            with open(marker_path, 'w', encoding='utf-8') as f:
                json.dump(info_data, f, indent=2)
        except OSError as e:
            logger.warning(
                '[GOGMarker] write %s: %s', marker_path, e,
            )
            return False
        return True

    async def regenerate_manifest(
        self, game_id: str, platform: str,
    ) -> None:
        """Regenerate manifest."""
        gogdl_bin = self._parent._gogdl_bin
        if not gogdl_bin:
            return
        try:
            async with self._parent._tokens.gogdl_credentials() as env:
                proc = await asyncio.create_subprocess_exec(
                    gogdl_bin, 'manifest', game_id, '--platform', platform,
                    env={**env},
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
        except OSError as e:
            logger.debug('[GOGMarker] manifest spawn: %s', e)


_ = cast
