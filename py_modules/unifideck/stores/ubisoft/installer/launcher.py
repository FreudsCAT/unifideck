"""launcher.py — Spawn UPC for "open launcher to install" flow.

# OP-56d | py_modules/unifideck/stores/ubisoft/installer/launcher.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ....core.types import Result
from .launch_env import UpcLaunchEnvBuildError

if TYPE_CHECKING:
    from .installer import UbisoftInstaller

logger = logging.getLogger(__name__)


class _LauncherInstall:
    """Launcher install."""

    def __init__(self, parent: UbisoftInstaller) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def open_launcher_for_install(self, game_id: str) -> Result:
        """Open launcher for install."""
        prefix_path = self._parent._paths.get_prefix_path(game_id)
        try:
            launch = self._parent._build_upc_launch_env(
                game_id, prefix_path, prefer_connect_exe=True,
            )
        except UpcLaunchEnvBuildError as e:
            return Result(success=False, error=e.error_code)
        install_id = (
            self._parent._id_map.resolve_install_id(game_id) or game_id
        )
        cmd = [
            launch.umu_run, launch.upc_path,
            f'uplay://install/{install_id}',
        ]
        return await self._spawn_and_monitor_upc(
            cmd, launch.env, game_id, prefix_path,
        )

    async def _spawn_and_monitor_upc(
        self,
        cmd: list[str],
        env: dict[str, str],
        game_id: str,
        prefix_path: str,
    ) -> Result:
        """Spawn and monitor UPC."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            return Result(success=False, error=f'spawn_failed:{e}')
        spawned_pid = proc.pid
        loop = asyncio.get_running_loop()
        loop.create_task(
            self.monitor_after_exit(
                game_id, spawned_pid, proc, prefix_path,
            ),
            name=f'ubisoft_upc_monitor:{game_id}',
        )
        return Result(success=True, data={'pid': spawned_pid})

    async def monitor_after_exit(
        self, game_id: str, spawned_pid: int,
        proc: asyncio.subprocess.Process, prefix_path: str,
    ) -> None:
        """Monitor after exit."""
        try:
            await proc.wait()
        except (OSError, asyncio.CancelledError):
            return
        try:
            self._parent._library._detector.get_installed_game_info(game_id)
        except Exception as e:
            logger.debug(
                '[Ubisoft.launcher] post-exit detection: %s', e,
            )
        try:
            self._parent._session.capture(prefix_path)
        except Exception as e:
            logger.debug(
                '[Ubisoft.launcher] post-exit session capture: %s', e,
            )
