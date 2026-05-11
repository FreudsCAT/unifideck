"""progress.py — Pipe gogdl's stdout into a progress callback.

# OP-51f | py_modules/unifideck/stores/gog/install/progress.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .installer import GOGInstaller

logger = logging.getLogger(__name__)
_GOGDL_STALL_TIMEOUT_S = 120.0
_ETA_RE = re.compile(r'ETA[: ]+(\d+):(\d+):(\d+)')
_SPEED_RE = re.compile(r'(\d+(?:\.\d+)?)\s*MiB/s')
_PROGRESS_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%')


class _GogdlProgressMonitor:
    """GOGDL progress monitor."""

    def __init__(self, parent: GOGInstaller) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def run_gogdl_with_progress(
        self,
        install_mode: str,
        game_id: str,
        platform: str,
        path: str,
        support_dir: str,
        languages: list[str],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        """Run GOGDL with progress."""
        cmd = self._build_gogdl_cmd(
            install_mode, game_id, platform, path, support_dir, languages,
        )
        proc = await self._spawn_gogdl(cmd)
        if proc is None:
            return False
        return await self._read_progress_loop(proc, progress_cb)

    def _build_gogdl_cmd(
        self,
        install_mode: str,
        game_id: str,
        platform: str,
        path: str,
        support_dir: str,
        languages: list[str],
    ) -> list[str]:
        """Build GOGDL cmd."""
        cmd = [
            self._parent._gogdl_bin, install_mode, game_id,
            '--platform', platform,
            '--path', path,
        ]
        if support_dir:
            cmd += ['--support', support_dir]
        for lang in languages:
            cmd += ['--lang', lang]
        return cmd

    async def _spawn_gogdl(
        self, cmd: list[str],
    ) -> asyncio.subprocess.Process | None:
        """Spawn GOGDL."""
        try:
            async with self._parent._tokens.gogdl_credentials() as env:
                return await asyncio.create_subprocess_exec(
                    *cmd, env={**env},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
        except OSError as e:
            logger.warning('[GOGProgress] spawn: %s', e)
            return None

    async def _read_progress_loop(
        self,
        proc: asyncio.subprocess.Process,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        """Read progress loop."""
        if proc.stdout is None:
            return await proc.wait() == 0
        progress: dict[str, Any] = {}
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=_GOGDL_STALL_TIMEOUT_S,
                )
            except TimeoutError:
                logger.warning('[GOGProgress] stalled, terminating')
                await self._terminate_gogdl(proc)
                return False
            if not line:
                break
            await self._handle_progress_line(
                line.decode('utf-8', errors='replace').strip(),
                progress, progress_cb,
            )
        return await proc.wait() == 0

    async def _handle_progress_line(
        self,
        line_str: str,
        progress: dict[str, Any],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Handle progress line."""
        self._parse_progress_line(line_str, progress)
        if progress_cb is not None and progress:
            await progress_cb({'phase': 'progress', **progress})

    @staticmethod
    def _parse_eta(line: str) -> int | None:
        """Parse eta."""
        m = _ETA_RE.search(line)
        if not m:
            return None
        h, mi, s = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + s

    @staticmethod
    def _parse_speed_mib(line: str) -> float | None:
        """Parse speed mib."""
        m = _SPEED_RE.search(line)
        return float(m.group(1)) if m else None

    @staticmethod
    def _parse_progress_line(
        line: str, progress: dict[str, Any],
    ) -> None:
        """Parse progress line."""
        m = _PROGRESS_RE.search(line)
        if m:
            try:
                progress['percent'] = float(m.group(1))
            except ValueError:
                pass
        eta = _GogdlProgressMonitor._parse_eta(line)
        if eta is not None:
            progress['eta_seconds'] = eta
        speed = _GogdlProgressMonitor._parse_speed_mib(line)
        if speed is not None:
            progress['speed_mib_per_s'] = speed

    @staticmethod
    async def _terminate_gogdl(proc: asyncio.subprocess.Process) -> None:
        """Terminate GOGDL."""
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def run_gogdl_repair_pass(
        self,
        game_id: str,
        platform: str,
        base_path: str,
        folder_name: str | None,
        preferred_lang: str,
    ) -> None:
        """Run GOGDL repair pass."""
        if not self._parent._gogdl_bin:
            return
        path = self._resolve_repair_path(game_id, base_path, folder_name)
        if not path:
            return
        try:
            async with self._parent._tokens.gogdl_credentials() as env:
                proc = await asyncio.create_subprocess_exec(
                    self._parent._gogdl_bin, 'repair', game_id,
                    '--platform', platform,
                    '--path', path,
                    '--lang', preferred_lang,
                    env={**env},
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
        except OSError as e:
            logger.debug('[GOGProgress] repair spawn: %s', e)

    @staticmethod
    def _resolve_repair_path(
        game_id: str, base_path: str, folder_name: str | None,
    ) -> str:
        """Resolve repair path."""
        if folder_name:
            return os.path.join(base_path, folder_name)
        return base_path
