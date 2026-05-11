"""update_op.py — Run UPC's per-game update.

# OP-56h | py_modules/unifideck/stores/ubisoft/installer/update_op.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ....core.types import InstallResult
from .launch_env import UpcLaunchEnvBuildError, _UpcLaunchEnv

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..id_map import UbisoftIdMap
    from ..paths import UbisoftPrefixPaths
    from ..session import UbisoftSession

_UPDATE_TIMEOUT_S = 4 * 60 * 60
logger = logging.getLogger(__name__)


class _UpdateOperation:
    """Update operation."""

    def __init__(
        self, *,
        id_map: UbisoftIdMap,
        paths: UbisoftPrefixPaths,
        session: UbisoftSession,
        build_launch_env: Callable[..., _UpcLaunchEnv],
    ) -> None:
        """Initialize the instance."""
        self._id_map = id_map
        self._paths = paths
        self._session = session
        self._build_launch_env = build_launch_env

    async def update(self, game_id: str) -> InstallResult:
        """Update."""
        prepared = self._prepare_launch(game_id)
        if isinstance(prepared, InstallResult):
            return prepared
        return await self._run_update_process(game_id, prepared)

    def _prepare_launch(
        self, game_id: str,
    ) -> _UpcLaunchEnv | InstallResult:
        """Prepare launch."""
        prefix_path = self._paths.get_prefix_path(game_id)
        try:
            return self._build_launch_env(
                game_id, prefix_path, prefer_connect_exe=True,
            )
        except UpcLaunchEnvBuildError as e:
            return InstallResult(
                success=False, store='ubisoft', game_id=game_id,
                error=e.error_code,
            )

    async def _run_update_process(
        self, game_id: str, launch_env: _UpcLaunchEnv,
    ) -> InstallResult:
        """Run update process."""
        install_id = self._id_map.resolve_install_id(game_id) or game_id
        cmd = [
            launch_env.umu_run, launch_env.upc_path,
            f'uplay://launch/{install_id}',
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=launch_env.env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=_UPDATE_TIMEOUT_S)
        except TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return InstallResult(
                success=False, store='ubisoft', game_id=game_id,
                error='update_timeout',
            )
        except OSError as e:
            return InstallResult(
                success=False, store='ubisoft', game_id=game_id,
                error=f'spawn_failed:{e}',
            )
        if proc.returncode != 0:
            return InstallResult(
                success=False, store='ubisoft', game_id=game_id,
                error=f'rc:{proc.returncode}',
            )
        return InstallResult(success=True, store='ubisoft', game_id=game_id)
