"""manual_ui.py — Drive UPC's GUI installer and detect completion.

# OP-56e | py_modules/unifideck/stores/ubisoft/installer/manual_ui.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from ....core.types import InstallResult
from ..config import UbisoftConfig
from ..id_map import UbisoftIdMap
from ..library import UbisoftLibrary
from ..library.detection_helpers import looks_like_game_install
from ..session import UbisoftSession
from . import registry as _reg

logger = logging.getLogger(__name__)
_MANUAL_INSTALL_TIMEOUT_S = 2 * 60 * 60
_MANUAL_INSTALL_POLL_INTERVAL_S = 10.0
_STABILITY_WAIT_MAX_POLLS = 360
_STABILITY_POLL_INTERVAL_S = 10.0
_STABILITY_STABLE_THRESHOLD = 3


class _ManualUiInstaller:
    """Manual UI installer."""

    def __init__(
        self,
        config: UbisoftConfig,
        library: UbisoftLibrary,
        id_map: UbisoftIdMap,
        session: UbisoftSession,
        active_install_pids: dict[str, int],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._library = library
        self._id_map = id_map
        self._session = session
        self._active_install_pids = active_install_pids

    async def install_via_upc_ui(
        self,
        *,
        game_id: str,
        game_name: str | None,
        prefix_path: str,
        upc_path: str,
        umu_run: str,
        python_bin: str,
        env: dict[str, str],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        install_path: str | None = None,
    ) -> InstallResult:
        """Install via UPC UI."""
        if install_path is None:
            install_path = self._config.default_install_base_expanded
        os.makedirs(install_path, exist_ok=True)
        install_base, dirs_before, upc_dirs_before = self._snapshot_pre_install(
            install_path, prefix_path,
        )
        self._capture_and_propagate_session(prefix_path)
        proc = await self._notify_and_spawn_upc(
            game_id=game_id, upc_path=upc_path, umu_run=umu_run,
            python_bin=python_bin, env=env, progress_cb=progress_cb,
        )
        self._active_install_pids[game_id] = proc.pid
        try:
            install_dir = await self._poll_for_new_install(
                proc=proc,
                install_base=install_base,
                dirs_before=dirs_before,
                upc_dirs_before=upc_dirs_before,
                progress_cb=progress_cb,
            )
            if install_dir is None:
                return InstallResult(
                    success=False, store='ubisoft', game_id=game_id,
                    error='install_dir_not_detected',
                )
            return await self._finalize_manual_install(
                game_id=game_id, game_name=game_name, install_dir=install_dir,
            )
        finally:
            self._active_install_pids.pop(game_id, None)
            await self._terminate_upc_gracefully(proc)

    async def _notify_and_spawn_upc(
        self,
        *,
        game_id: str,
        upc_path: str,
        umu_run: str,
        python_bin: str,
        env: dict[str, str],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> asyncio.subprocess.Process:
        """Notify and spawn UPC."""
        if progress_cb is not None:
            await progress_cb({
                'phase': 'spawn', 'game_id': game_id,
                'message': 'Launching Ubisoft Connect installer',
            })
        install_id = self._id_map.resolve_install_id(game_id) or game_id
        cmd = [
            umu_run, upc_path, f'uplay://install/{install_id}',
        ]
        return await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    def _snapshot_pre_install(
        self, install_path: str | None, prefix_path: str,
    ) -> tuple[str, set[str], dict[str, set[str]]]:
        """Snapshot pre install."""
        base, dirs = self._snapshot_install_base(install_path)
        upc_dirs = self._snapshot_upc_game_dirs(prefix_path)
        return base, dirs, upc_dirs

    def _capture_and_propagate_session(self, prefix_path: str) -> None:
        """Capture and propagate session."""
        try:
            self._session.capture(prefix_path)
            self._session.propagate_all_to_all()
        except Exception as e:
            logger.debug('[Ubisoft.manual] session propagate: %s', e)

    def _snapshot_install_base(
        self, install_path: str | None,
    ) -> tuple[str, set]:
        """Snapshot install base."""
        if not install_path or not os.path.isdir(install_path):
            return install_path or '', set()
        try:
            return install_path, set(os.listdir(install_path))
        except OSError:
            return install_path, set()

    @staticmethod
    async def _terminate_upc_gracefully(
        proc: asyncio.subprocess.Process, timeout: float = 15.0,
    ) -> None:
        """Terminate UPC gracefully."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _finalize_manual_install(
        self, *, game_id: str, game_name: str | None, install_dir: str,
    ) -> InstallResult:
        """Finalize manual install."""
        executable = self._library.find_game_executable(install_dir) or ''
        await self._library.write_install_marker(
            game_id, install_dir, executable, game_name or '',
        )
        install_id = self._id_map.resolve_install_id(game_id)
        if install_id:
            try:
                _reg.inject_install_registry(
                    prefix_path=self._config.prefixes_dir_expanded,
                    install_id=install_id, install_dir=install_dir,
                )
            except Exception as e:
                logger.debug('[Ubisoft.manual] reg inject: %s', e)
        return InstallResult(
            success=True, store='ubisoft', game_id=game_id,
            install_path=install_dir,
        )

    @staticmethod
    def _snapshot_upc_game_dirs(prefix_path: str) -> dict[str, set]:
        """Snapshot UPC game dirs."""
        roots = (
            os.path.join(
                prefix_path, 'drive_c', 'Program Files (x86)',
                'Ubisoft', 'Ubisoft Game Launcher', 'games',
            ),
            os.path.join(
                prefix_path, 'pfx', 'drive_c', 'Program Files (x86)',
                'Ubisoft', 'Ubisoft Game Launcher', 'games',
            ),
        )
        out: dict[str, set] = {}
        for root in roots:
            if os.path.isdir(root):
                try:
                    out[root] = set(os.listdir(root))
                except OSError:
                    out[root] = set()
        return out

    async def _poll_for_new_install(
        self,
        *,
        proc: asyncio.subprocess.Process,
        install_base: str,
        dirs_before: set,
        upc_dirs_before: dict[str, set],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> str | None:
        """Poll for new install."""
        deadline = _MANUAL_INSTALL_TIMEOUT_S
        elapsed = 0.0
        while elapsed < deadline:
            for base, before in (
                (install_base, dirs_before),
                *upc_dirs_before.items(),
            ):
                new_dir = self._check_new_dirs(base, before)
                if new_dir and looks_like_game_install(new_dir):
                    await self._notify_install_detected(new_dir, progress_cb)
                    await self._wait_for_install_completion(new_dir, progress_cb)
                    return new_dir
            if proc.returncode is not None:
                return None
            await asyncio.sleep(_MANUAL_INSTALL_POLL_INTERVAL_S)
            elapsed += _MANUAL_INSTALL_POLL_INTERVAL_S
        return None

    @staticmethod
    async def _notify_install_detected(
        install_dir: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Notify install detected."""
        if progress_cb is None:
            return
        await progress_cb({
            'phase': 'detected', 'install_dir': install_dir,
            'message': 'Install detected; waiting for completion',
        })

    def _check_new_dirs(self, base: str, before: set) -> str | None:
        """Check new dirs."""
        if not base or not os.path.isdir(base):
            return None
        try:
            current = set(os.listdir(base))
        except OSError:
            return None
        new = current - before
        for entry in sorted(new):
            full = os.path.join(base, entry)
            if os.path.isdir(full):
                return full
        return None

    async def _wait_for_install_completion(
        self,
        install_dir: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Wait for install completion."""
        last_size = -1
        stable_count = 0
        for _ in range(_STABILITY_WAIT_MAX_POLLS):
            size = _reg.get_directory_size(install_dir)
            if size == last_size:
                stable_count += 1
                if stable_count >= _STABILITY_STABLE_THRESHOLD:
                    return
            else:
                stable_count = 0
                last_size = size
                if progress_cb is not None:
                    await progress_cb({
                        'phase': 'progress', 'install_dir': install_dir,
                        'bytes_written': size,
                    })
            await asyncio.sleep(_STABILITY_POLL_INTERVAL_S)
