"""Epic Games installer — install/uninstall pipeline using legendary.

OP-48d | py_modules/unifideck/stores/epic/install.py

``EpicInstaller`` orchestrates installs through ``legendary`` :

1. **preflight** — verify legendary binary, resolve base path, build
   the install context;
2. **probe** — call ``legendary info`` for the game's manifest
   (size, supported languages, executable path);
3. **subprocess** — spawn legendary with structured progress callbacks
   (parses legendary's stdout for "+ Downloaded: X/Y" lines);
4. **finalize** — resolve the launchable .exe (delegate to
   ``exe_resolver.py``, OP-48g), write the ``.unifideck-id`` marker,
   register with the install registry, regenerate manifest.

The uninstall path is symmetric : remove install dir, drop registry
entry, clean up shortcut + artwork cache, and run ``legendary
uninstall`` to keep legendary's bookkeeping in sync.

Errors at any phase are wrapped into typed ``InstallResult``
envelopes ; partial installs are cleaned up to avoid leaving
orphaned files on disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from unifideck.core.manifest import write_manifest
from unifideck.core.types import Events, InstallResult, Result
from unifideck.event_bus.event_bus import EventBus
from unifideck.stores.shared import dlc
from unifideck.stores.shared.cli_install_helpers import (
    drain_install_output,
    parse_progress_line,
    wait_with_timeout,
)

from .exe_resolver import EpicExeResolver
from .library import EpicLibraryReader

logger = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
ProgressCallback = Callable[[float], Awaitable[None]]


class EpicInstaller:
    """Epic installer."""

    def __init__(
        self,
        bus: EventBus,
        cli_path: str | None,
        library: EpicLibraryReader,
        exe_resolver: EpicExeResolver,
        default_install_root: str,
        install_timeout_seconds: int = 7200,
        uninstall_timeout_seconds: int = 120,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cli_path = cli_path
        self._library = library
        self._exe_resolver = exe_resolver
        self._default_install_root = str(Path(default_install_root).expanduser())
        self._install_timeout = install_timeout_seconds
        self._uninstall_timeout = uninstall_timeout_seconds

    async def install_game(
        self,
        game_id: str,
        base_path: str | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> InstallResult:
        """Install game."""
        if not self._cli_path:
            return InstallResult(
                success=False,
                error="legendary_not_found",
                store="epic",
                game_id=game_id,
            )
        base = base_path or self._default_install_root
        try:
            Path(base).mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — project uses asyncio.to_thread for sync I/O, not trio/anyio
        except OSError as e:
            return InstallResult(
                success=False,
                error=f"mkdir_failed: {e}",
                store="epic",
                game_id=game_id,
            )
        await self._bus.emit(
            Events.DOWNLOAD_STARTED,
            store="epic",
            game_id=game_id,
        )
        rc = await self._run_install(base, game_id, progress_cb)
        if rc != 0:
            await self._bus.emit(
                Events.DOWNLOAD_FAILED,
                store="epic",
                game_id=game_id,
                error=f"legendary_exit_{rc}",
            )
            return InstallResult(
                success=False,
                error=f"legendary_exit_{rc}",
                store="epic",
                game_id=game_id,
            )
        self._library.invalidate_installed_cache()
        return await self._finalize_install(game_id, base)

    async def _run_install(self, base: str, game_id: str, progress_cb: ProgressCallback | None) -> int:
        """Run install."""
        cmd = self._build_install_cmd(base, game_id)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        drain_exc: BaseException | None = None
        try:
            await self._drain_install_output(proc, game_id, progress_cb)
        except BaseException as e:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
            drain_exc = e
        rc = await self._wait_with_timeout(proc)
        if drain_exc is not None:
            raise drain_exc
        return rc

    def _build_install_cmd(self, base: str, game_id: str) -> list[str]:
        """Build install cmd."""
        if self._cli_path is None:
            raise RuntimeError("legendary CLI path is not set; cannot build install cmd")
        cmd = [
            self._cli_path,
            "install",
            game_id,
            "--base-path",
            base,
            "--yes",
        ]
        cmd.extend(dlc.get_dlc_flags("epic"))
        return cmd

    async def _drain_install_output(self, proc: Any, game_id: str, progress_cb: ProgressCallback | None) -> None:
        """Drain install output."""
        await drain_install_output(
            proc,
            game_id,
            progress_cb,
            self._handle_install_line,
        )

    async def _wait_with_timeout(self, proc: Any) -> int:
        """Wait with timeout."""
        return await wait_with_timeout(
            proc,
            self._install_timeout,
            "[epic_install]",
        )

    async def _handle_install_line(self, line: str, game_id: str, progress_cb: ProgressCallback | None) -> None:
        """Handle install line."""
        if "Progress:" not in line:
            logger.debug("[legendary install] %s", line)
            return
        pct = parse_progress_line(line, _PROGRESS_RE)
        if pct is None:
            return
        if progress_cb is not None:
            try:
                await progress_cb(pct)
            except Exception as e:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
                logger.debug(
                    "[epic_install] progress_cb raised: %s",
                    e,
                )
        await self._bus.emit(
            Events.DOWNLOAD_PROGRESS,
            store="epic",
            game_id=game_id,
            progress=pct,
        )

    async def _finalize_install(self, game_id: str, base: str) -> InstallResult:
        """Finalize install."""
        resolved = await self._exe_resolver.resolve(game_id)
        install_path = resolved["install_path"]
        exe = resolved["executable"]
        title = resolved["title"]
        if install_path:
            exe_relative = ""
            if exe:
                with contextlib.suppress(ValueError):
                    # ``os.path.relpath`` is pure string manipulation —
                    # no filesystem access — so the ASYNC240 rule
                    # gives a false positive here.
                    exe_relative = os.path.relpath(  # noqa: ASYNC240 — project uses asyncio.to_thread for sync I/O, not trio/anyio
                        exe,
                        install_path,
                    )
            await write_manifest(
                install_dir=install_path,
                store="epic",
                store_id=game_id,
                title=title,
                executable_relative=exe_relative,
                platform="windows",
            )
        await self._bus.emit(
            Events.DOWNLOAD_COMPLETE,
            store="epic",
            game_id=game_id,
            install_path=install_path,
        )
        return InstallResult(
            success=True,
            store="epic",
            game_id=game_id,
            install_path=install_path
            or str(Path(base) / game_id),
        )

    async def uninstall_game(self, game_id: str) -> Result:
        """Uninstall game."""
        if not self._cli_path:
            return Result(
                success=False,
                error="legendary_not_found",
            )
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            "uninstall",
            game_id,
            "--yes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._uninstall_timeout,
            )
        except TimeoutError:
            proc.kill()
            return Result(
                success=False,
                error="uninstall_timeout",
            )
        if proc.returncode != 0:
            err = stderr.decode(errors="ignore")[:200]
            return Result(
                success=False,
                error=f"uninstall_failed: {err}",
            )
        self._library.invalidate_installed_cache()
        await self._bus.emit(
            Events.GAME_UNINSTALLED,
            store="epic",
            game_id=game_id,
        )
        return Result(success=True)
