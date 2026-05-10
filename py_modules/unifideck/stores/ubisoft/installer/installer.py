"""installer.py — Public ``UbisoftInstaller`` surface.

# OP-56a | py_modules/unifideck/stores/ubisoft/installer/installer.py | Depends: OP-55a
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

from ....core.types import InstallResult, Result
from ..binaries import UbisoftBinaryResolver
from ..config import UbisoftConfig
from ..id_map import UbisoftIdMap
from ..library import UbisoftLibrary
from ..paths import UbisoftPrefixPaths
from ..session import UbisoftSession
from . import registry as _reg
from .launch_env import UpcLaunchEnvBuildError, _UpcLaunchEnv
from .launcher import _LauncherInstall
from .manual_ui import _ManualUiInstaller
from .registry import _ShortcutRegistry
from .uninstall import _UninstallPipeline
from .update_op import _UpdateOperation

logger = logging.getLogger(__name__)
_UPDATE_TIMEOUT_S = 4 * 60 * 60


class UbisoftInstaller:
    """Ubisoft installer."""

    def __init__(
        self,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        binaries: UbisoftBinaryResolver,
        id_map: UbisoftIdMap,
        session: UbisoftSession,
        library: UbisoftLibrary,
        bootstrap_game_prefix: Callable[[str], Awaitable[bool]],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._binaries = binaries
        self._id_map = id_map
        self._session = session
        self._library = library
        self._bootstrap_game_prefix = bootstrap_game_prefix
        self._active_install_pids: dict[str, int] = {}
        self._shortcut_registry = _ShortcutRegistry(config)
        self._launcher = _LauncherInstall(self)
        self._manual_ui = _ManualUiInstaller(
            config, library, id_map, session, self._active_install_pids,
        )
        self._uninstall = _UninstallPipeline(self)
        self._update_op = _UpdateOperation(
            id_map=id_map, paths=paths, session=session,
            build_launch_env=self._build_upc_launch_env,
        )

    async def uninstall_game(
        self, game_id: str, *, delete_prefix: bool = False,
    ) -> Result:
        """Uninstall game."""
        return await self._uninstall.uninstall_game(
            game_id, delete_prefix=delete_prefix,
        )

    async def open_launcher_for_install(self, game_id: str) -> Result:
        """Open launcher for install."""
        return await self._launcher.open_launcher_for_install(game_id)

    def _build_upc_launch_env(
        self,
        game_id: str,
        prefix_path: str,
        *,
        prefer_connect_exe: bool = False,
        upc_missing_error: str = 'upc_exe_not_found',
    ) -> _UpcLaunchEnv:
        """Build UPC launch env."""
        finder = (
            self._paths.find_connect_exe if prefer_connect_exe
            else self._paths.find_upc_exe
        )
        upc_path = finder(prefix_path)
        if not upc_path:
            upc_path = (
                self._paths.find_upc_exe(prefix_path)
                if prefer_connect_exe else None
            )
        if not upc_path:
            raise UpcLaunchEnvBuildError(upc_missing_error)
        umu_run = self._binaries.find_umu_run()
        if not umu_run:
            raise UpcLaunchEnvBuildError('umu_run_not_found')
        python_bin = self._binaries.find_python()
        proton_path = self._binaries.find_proton_path()
        steam_window_env = self._build_steam_window_env(
            f'ubisoft:{game_id}',
        )
        env = self._binaries.build_umu_env(
            wineprefix=prefix_path,
            gameid=f'umu-ubisoft-{game_id}',
            proton_path=proton_path,
            store_game_id=f'ubisoft:{game_id}',
            steam_window_env=steam_window_env,
        )
        return _UpcLaunchEnv(
            upc_path=upc_path, umu_run=umu_run,
            python_bin=python_bin, env=env,
        )

    async def install_game(
        self,
        game_id: str,
        *,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        install_path: str | None = None,
    ) -> InstallResult:
        """Install game."""
        prefix_path = self._paths.get_prefix_path(game_id)
        ok = await self._bootstrap_game_prefix(game_id)
        if not ok:
            return InstallResult(
                success=False, store='ubisoft', game_id=game_id,
                error='prefix_bootstrap_failed',
            )
        try:
            launch = self._build_upc_launch_env(
                game_id, prefix_path, prefer_connect_exe=True,
            )
        except UpcLaunchEnvBuildError as e:
            return InstallResult(
                success=False, store='ubisoft', game_id=game_id,
                error=e.error_code,
            )
        info = self._library._detector._id_map.get_entry(game_id)
        return await self._manual_ui.install_via_upc_ui(
            game_id=game_id,
            game_name=info.get('name'),
            prefix_path=prefix_path,
            upc_path=launch.upc_path,
            umu_run=launch.umu_run,
            python_bin=launch.python_bin,
            env=launch.env,
            progress_cb=progress_cb,
            install_path=install_path,
        )

    def is_install_session_active(self, game_id: str) -> bool:
        """Is install session active."""
        return game_id in self._active_install_pids

    async def cancel_install_session(self, game_id: str) -> Result:
        """Cancel install session."""
        pid = self._active_install_pids.pop(game_id, None)
        if pid is None:
            return Result(success=False, error='no_active_session')
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            return Result(success=True)
        except OSError as e:
            return Result(success=False, error=f'kill_failed:{e}')
        return Result(success=True)

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        # UPC drives updates internally; we surface no proactive list.
        return []

    async def update_game(self, game_id: str) -> InstallResult:
        """Update game."""
        return await self._update_op.update(game_id)

    def inject_install_registry(
        self, prefix_path: str, install_id: str, install_dir: str,
    ) -> None:
        """Inject install registry."""
        _reg.inject_install_registry(prefix_path, install_id, install_dir)

    def kill_upc_processes(self) -> None:
        """Kill UPC processes."""
        try:
            subprocess.run(
                ['pkill', '-f', 'UbisoftConnect.exe'],
                check=False, timeout=5,
            )
            subprocess.run(
                ['pkill', '-f', 'upc.exe'],
                check=False, timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug('[Ubisoft.installer] kill_upc_processes: %s', e)

    def _build_steam_window_env(
        self, store_game_id: str | None,
    ) -> dict[str, str]:
        """Build steam window env."""
        appid = self._shortcut_registry.resolve_shortcut_appid(store_game_id)
        if not appid:
            return {
                'SteamGameId': '0',
                'STEAM_COMPAT_APP_ID': '0',
                'SteamAppId': '0',
                'UMU_STEAM_GAME_ID': '0',
            }
        encoded = str((appid << 32) | 0x02000000)
        appid_str = str(appid)
        return {
            'SteamGameId': appid_str,
            'STEAM_COMPAT_APP_ID': appid_str,
            'SteamAppId': appid_str,
            'UMU_STEAM_GAME_ID': encoded,
        }
