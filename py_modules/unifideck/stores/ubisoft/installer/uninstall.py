"""uninstall.py — Multi-step uninstall pipeline.

# OP-56g | py_modules/unifideck/stores/ubisoft/installer/uninstall.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ....core.types import Result
from . import registry as _reg
from .launch_env import UpcLaunchEnvBuildError

if TYPE_CHECKING:
    from .installer import UbisoftInstaller

logger = logging.getLogger(__name__)
_PROTOCOL_UNINSTALL_TIMEOUT_S = 60.0
_DELETE_MIN_PATH_DEPTH = 4


class _UninstallPipeline:
    """Uninstall pipeline."""

    def __init__(self, parent: UbisoftInstaller) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def uninstall_game(
        self, game_id: str, *, delete_prefix: bool = False,
    ) -> Result:
        """Uninstall game."""
        prefix_path, install_path, install_id = (
            self.resolve_uninstall_targets(game_id)
        )
        protocol_done = False
        if install_id:
            protocol_done = await self.attempt_protocol_uninstall(
                game_id, prefix_path, install_id, delete_prefix,
            )
        install_path = self.refresh_install_path(
            game_id, prefix_path, install_path,
        )
        deleted_dir = await self.delete_game_directory(
            install_path, prefix_path, delete_prefix,
        )
        prefix_deleted, _err = await self.delete_prefix_if_requested(
            prefix_path, delete_prefix,
        )
        self.post_uninstall_cleanup(
            game_id, prefix_path, install_id, prefix_deleted,
        )
        return Result(
            success=True,
            data={
                'protocol_uninstall': protocol_done,
                'deleted_directory': deleted_dir,
                'prefix_deleted': prefix_deleted,
            },
        )

    def resolve_uninstall_targets(
        self, game_id: str,
    ) -> tuple[str, str | None, str | None]:
        """Resolve uninstall targets."""
        prefix_path = self._parent._paths.get_prefix_path(game_id)
        info = self._parent._library.get_installed_game_info(game_id) or {}
        install_path = info.get('install_path')
        install_id = (
            self._parent._id_map.get_entry(game_id).get('install_id')
            or info.get('install_id')
        )
        return prefix_path, install_path, install_id

    async def attempt_protocol_uninstall(
        self, game_id: str, prefix_path: str,
        install_id: str | None, delete_prefix: bool,
    ) -> bool:
        """Attempt protocol uninstall."""
        if not install_id:
            return False
        try:
            return await self.try_protocol_uninstall(
                game_id, prefix_path, install_id,
            )
        except UpcLaunchEnvBuildError as e:
            logger.warning(
                '[Ubisoft.uninstall] protocol uninstall env error: %s',
                e.error_code,
            )
            return False
        except Exception as e:
            logger.warning(
                '[Ubisoft.uninstall] protocol uninstall failed: %s', e,
            )
            return False

    def refresh_install_path(
        self, game_id: str, prefix_path: str, install_path: str | None,
    ) -> str | None:
        """Refresh install path."""
        if install_path and Path(install_path).is_dir():
            return install_path
        info = self._parent._library.get_installed_game_info(game_id)
        return (info or {}).get('install_path') or install_path

    async def delete_game_directory(
        self, install_path: str | None, prefix_path: str, delete_prefix: bool,
    ) -> str | None:
        """Delete game directory."""
        if not install_path:
            return None
        if not Path(install_path).is_dir():
            return None
        if not self._is_path_safe_to_delete(install_path, 'install_path'):
            return None
        if not delete_prefix:
            inside_prefix = Path(install_path).is_relative_to(prefix_path)
            if not inside_prefix:
                logger.info(
                    '[Ubisoft.uninstall] skip external dir %s', install_path,
                )
                return None
        ok = await self.delete_tree_with_retries(install_path, 'install_dir')
        return install_path if ok else None

    async def delete_prefix_if_requested(
        self, prefix_path: str, delete_prefix: bool,
    ) -> tuple[bool, str | None]:
        """Delete prefix if requested."""
        if not delete_prefix:
            return False, None
        if not Path(prefix_path).is_dir():
            return False, None
        if not self._is_path_safe_to_delete(prefix_path, 'prefix'):
            return False, 'unsafe'
        ok = await self.delete_tree_with_retries(prefix_path, 'prefix')
        return ok, None if ok else 'failed'

    def post_uninstall_cleanup(
        self, game_id: str, prefix_path: str,
        install_id: str | None, prefix_deleted: bool,
    ) -> None:
        """Post uninstall cleanup."""
        if not prefix_deleted and install_id:
            try:
                _reg.clean_install_registry(prefix_path, install_id)
            except Exception as e:
                logger.debug('[Ubisoft.uninstall] reg clean: %s', e)

    async def try_protocol_uninstall(
        self, game_id: str, prefix_path: str, install_id: str,
    ) -> bool:
        """Try protocol uninstall."""
        env_b = self._parent._build_upc_launch_env(
            game_id, prefix_path, prefer_connect_exe=True,
        )
        cmd = [
            env_b.umu_run, env_b.upc_path,
            f'uplay://uninstall/{install_id}',
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env_b.env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(
                proc.wait(), timeout=_PROTOCOL_UNINSTALL_TIMEOUT_S,
            )
        except (TimeoutError, OSError):
            return False
        return True

    @staticmethod
    def _is_path_safe_to_delete(target_path: str, label: str) -> bool:
        """Is path safe to delete."""
        try:
            real = Path(target_path).resolve()
        except OSError:
            return False
        depth = len(real.parts)
        if depth < _DELETE_MIN_PATH_DEPTH:
            logger.warning(
                '[Ubisoft.uninstall] refusing %s with depth %d: %s',
                label, depth, real,
            )
            return False
        return True

    @staticmethod
    async def delete_tree_with_retries(
        target_path: str, label: str, *, retries: int = 3,
    ) -> bool:
        """Delete tree with retries."""
        for attempt in range(1, retries + 1):
            try:
                await asyncio.to_thread(
                    shutil.rmtree, target_path, ignore_errors=False,
                )
                return True
            except OSError as e:
                logger.warning(
                    '[Ubisoft.uninstall] delete %s attempt %d: %s',
                    label, attempt, e,
                )
                await asyncio.sleep(1.0)
        try:
            await asyncio.to_thread(
                shutil.rmtree, target_path, ignore_errors=True,
            )
            return not Path(target_path).exists()
        except OSError:
            return False
