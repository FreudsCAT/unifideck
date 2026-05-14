"""
UPC manual-UI driver — watches UPC windows and detects install completion.

OP-56e | py_modules/unifideck/stores/ubisoft/installer/manual_ui.py

UPC has no silent-install flag, so the installer must be driven by the
user pressing through the wizard. Once the wizard finishes UPC starts
a service-mode background loop which makes it hard to know when the
install is actually done.

This module exposes ``_ManualInstallDriver`` which:

* snapshots the ``drive_c/Program Files (x86)/.../games/`` directory
  *before* the install (``_snapshot_upc_game_dirs``);
* watches the parent of the install_base for new game-install dirs
  (``_check_new_dirs``);
* uses heuristics from ``looks_like_game_install`` to confirm the
  new directory really is a game install (not a transient temp dir).

Returns the detected install path or ``None`` on timeout.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from unifideck.core.types import InstallResult
from unifideck.stores.ubisoft.config import UbisoftConfig
from unifideck.stores.ubisoft.id_map import UbisoftIdMap
from unifideck.stores.ubisoft.library import UbisoftLibrary
from unifideck.stores.ubisoft.library.detection_helpers import looks_like_game_install
from unifideck.stores.ubisoft.session import UbisoftSession

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
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
        install_path: str | None,
    ) -> InstallResult:
        """Install via UPC UI."""
        logger.info(
            "[UbisoftInstaller] install_id unavailable for %s "
            "— launching UPC for manual install",
            game_id,
        )
        self._session.inject_into_prefix(prefix_path)
        install_base, dirs_before, upc_dirs_before = self._snapshot_pre_install(
            install_path, prefix_path
        )
        proc = await self._notify_and_spawn_upc(
            game_id=game_id,
            upc_path=upc_path,
            umu_run=umu_run,
            python_bin=python_bin,
            env=env,
            progress_cb=progress_cb,
        )
        install_dir = await self._poll_for_new_install(
            proc=proc,
            install_base=install_base,
            dirs_before=dirs_before,
            upc_dirs_before=upc_dirs_before,
            progress_cb=progress_cb,
        )
        await self._terminate_upc_gracefully(proc)
        self._active_install_pids.pop(game_id, None)
        self._capture_and_propagate_session(prefix_path)
        if not install_dir:
            return InstallResult(
                success=False,
                store="ubisoft",
                game_id=game_id,
                error="no_install_detected",
            )
        return await self._finalize_manual_install(
            game_id=game_id,
            game_name=game_name,
            install_dir=install_dir,
        )

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
        if progress_cb:
            await progress_cb(
                {
                    "status": "waiting",
                    "message": (
                        "Ubisoft Connect is opening. Please "
                        "install the game from the UPC interface."
                    ),
                    "progress": 0,
                }
            )
        logger.info(
            "[UbisoftInstaller] launching UPC for manual install",
        )
        proc = await asyncio.create_subprocess_exec(
            python_bin,
            umu_run,
            upc_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active_install_pids[game_id] = proc.pid
        return proc

    def _snapshot_pre_install(
        self,
        install_path: str | None,
        prefix_path: str,
    ) -> tuple[str, set[str], dict[str, set[str]]]:
        """Snapshot pre install."""
        install_base, dirs_before = self._snapshot_install_base(
            install_path,
        )
        upc_dirs_before = self._snapshot_upc_game_dirs(prefix_path)
        return install_base, dirs_before, upc_dirs_before

    def _capture_and_propagate_session(
        self,
        prefix_path: str,
    ) -> None:
        """Capture and propagate session."""
        if self._session.capture(prefix_path):
            self._session.propagate_all_to_all()

    def _snapshot_install_base(
        self,
        install_path: str | None,
    ) -> tuple[str, set]:
        """Snapshot install base."""
        install_base = install_path or self._config.default_install_base_expanded
        os.makedirs(install_base, exist_ok=True)
        dirs_before: set = set()
        try:
            dirs_before = set(os.listdir(install_base))
        except OSError:
            pass
        return install_base, dirs_before

    @staticmethod
    async def _terminate_upc_gracefully(
        proc: asyncio.subprocess.Process,
        timeout: float = 15.0,
    ) -> None:
        """Terminate UPC gracefully."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(
                proc.wait(),
                timeout=timeout,
            )
        except (TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _finalize_manual_install(
        self,
        *,
        game_id: str,
        game_name: str | None,
        install_dir: str,
    ) -> InstallResult:
        """Finalize manual install."""
        exe = self._library.find_game_executable(install_dir)
        await self._library.write_install_marker(
            space_id=game_id,
            install_path=install_dir,
            executable=exe or "",
            game_title=game_name or "",
        )
        final_size = _reg.get_directory_size(install_dir)
        logger.info(
            "[UbisoftInstaller] manual install complete: %s (%.0f MB)",
            install_dir,
            final_size / (1024 * 1024),
        )
        try:
            await self._id_map.refresh_from_configurations()
        except Exception as e:
            logger.debug(
                "[UbisoftInstaller] id_map refresh after install failed: %s",
                e,
            )
        return InstallResult(
            success=True,
            store="ubisoft",
            game_id=game_id,
            install_path=install_dir,
            size_bytes=final_size,
            metadata={"executable": exe},
        )

    @staticmethod
    def _snapshot_upc_game_dirs(
        prefix_path: str,
    ) -> dict[str, set]:
        """Snapshot UPC game dirs."""
        upc_games_rel = os.path.join(
            "drive_c",
            "Program Files (x86)",
            "Ubisoft",
            "Ubisoft Game Launcher",
            "games",
        )
        candidates = (
            os.path.join(prefix_path, upc_games_rel),
            os.path.join(prefix_path, "pfx", upc_games_rel),
        )
        snapshots: dict[str, set] = {}
        for gdir in candidates:
            if os.path.isdir(gdir):
                try:
                    snapshots[gdir] = set(os.listdir(gdir))
                except OSError:
                    pass
        return snapshots

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
        install_dir: str | None = None
        max_polls = int(
            _MANUAL_INSTALL_TIMEOUT_S / _MANUAL_INSTALL_POLL_INTERVAL_S,
        )
        for iteration in range(max_polls):
            await asyncio.sleep(
                _MANUAL_INSTALL_POLL_INTERVAL_S,
            )
            install_dir = self._check_new_dirs(
                install_base,
                dirs_before,
            )
            if not install_dir:
                for gdir, before in upc_dirs_before.items():
                    found = self._check_new_dirs(gdir, before)
                    if found:
                        install_dir = found
                        break
            if install_dir:
                logger.info(
                    "[UbisoftInstaller] detected install at %s",
                    install_dir,
                )
                await self._notify_install_detected(
                    install_dir,
                    progress_cb,
                )
                await self._wait_for_install_completion(
                    install_dir,
                    progress_cb,
                )
                return install_dir
            if proc.returncode is not None:
                logger.info(
                    "[UbisoftInstaller] UPC exited rc=%d",
                    proc.returncode,
                )
                return None
            if progress_cb and iteration % 6 == 0:
                await progress_cb(
                    {
                        "status": "waiting",
                        "message": (
                            "Waiting for game installation in Ubisoft Connect…"
                        ),
                        "progress": 0,
                    }
                )
        return None

    @staticmethod
    async def _notify_install_detected(
        install_dir: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Notify install detected."""
        if not progress_cb:
            return
        await progress_cb(
            {
                "status": "installing",
                "message": (f"Game detected at {os.path.basename(install_dir)}"),
                "progress": 50,
            }
        )

    def _check_new_dirs(
        self,
        base: str,
        before: set,
    ) -> str | None:
        """Check new dirs."""
        try:
            now = set(os.listdir(base))
        except OSError:
            return None
        new_dirs = now - before
        for d in new_dirs:
            candidate = os.path.join(base, d)
            if os.path.isdir(candidate) and looks_like_game_install(candidate):
                return candidate
        return None

    async def _wait_for_install_completion(
        self,
        install_dir: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Wait for install completion."""
        prev_size = 0
        stable_count = 0
        for _ in range(_STABILITY_WAIT_MAX_POLLS):
            await asyncio.sleep(_STABILITY_POLL_INTERVAL_S)
            curr_size = _reg.get_directory_size(install_dir)
            if curr_size == prev_size and curr_size > 0:
                stable_count += 1
                if stable_count >= _STABILITY_STABLE_THRESHOLD:
                    break
                stable_count = 0
                prev_size = curr_size
                if progress_cb and curr_size > 0:
                    await progress_cb(
                        {
                            "status": "installing",
                            "message": (
                                f"Installing… ({curr_size / (1024**3):.1f} GB)"
                            ),
                            "progress": min(
                                90,
                                50 + stable_count * 10,
                            ),
                        }
                    )
