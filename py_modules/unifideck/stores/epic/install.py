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
import json
import logging
import os
import re
import shutil
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


def _legendary_config_dir() -> Path:
    """Return legendary's config dir (honours ``LEGENDARY_CONFIG_DIR``)."""
    env = os.environ.get("LEGENDARY_CONFIG_DIR")
    return (
        Path(env).expanduser() if env
        else Path("~/.config/legendary").expanduser()
    )


def _read_legendary_install_path(game_id: str) -> str | None:
    """Read a game's ``install_path`` from legendary's ``installed.json``.

    A local file read — no catalog/network call — so it works even when
    ``legendary uninstall`` 401s on Epic's catalog API and bails out.
    """
    path = _legendary_config_dir() / "installed.json"
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(game_id) if isinstance(data, dict) else None
    if isinstance(entry, dict):
        p = entry.get("install_path")
        return p if isinstance(p, str) and p else None
    return None


def _purge_legendary_install_entry(game_id: str) -> None:
    """Drop a game from legendary's ``installed.json``.

    ``legendary uninstall`` leaves the entry behind when its catalog
    lookup fails (HTTP 401), which makes the next library sync re-flag
    the game installed (the Epic library derives install state from
    ``legendary list-installed``). Removing the row keeps it honest.
    """
    path = _legendary_config_dir() / "installed.json"
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and game_id in data:
        data.pop(game_id, None)
        try:
            with path.open("w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning(
                "[EpicUninstall] could not rewrite installed.json: %s", e,
            )


def _is_safe_to_delete(p: Path) -> bool:
    """Guard against ``rmtree`` on the home dir, ``/``, or shallow roots."""
    resolved = p.resolve()
    home = Path.home().resolve()
    if resolved in (home, Path("/")) or len(resolved.parts) < 3:
        return False
    return True


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
        logger.info("[EpicInstall] install_game game_id=%s base_path=%s cli_path=%s",
                     game_id, base_path, self._cli_path)
        if not self._cli_path:
            logger.error("[EpicInstall] legendary CLI not found at %s", self._cli_path)
            return InstallResult(
                success=False,
                error="legendary_not_found",
                store="epic",
                game_id=game_id,
            )
        base = base_path or self._default_install_root
        try:
            await asyncio.to_thread(lambda: Path(base).mkdir(parents=True, exist_ok=True))
        except OSError as e:
            logger.exception("[EpicInstall] mkdir failed: %s", base)
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
        logger.info("[EpicInstall] running legendary install %s -> %s", game_id, base)
        rc = await self._run_install(base, game_id, progress_cb)
        logger.info("[EpicInstall] legendary exit_code=%d", rc)
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
        logger.info("[EpicInstall] executing: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        drain_exc: BaseException | None = None
        try:
            await self._drain_install_output(proc, game_id, progress_cb)
        except BaseException as e:
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
            except Exception as e:
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
                    exe_relative = os.path.relpath(  # noqa: ASYNC240 — pure string op, no I/O
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

    async def uninstall_game(
        self, game_id: str, delete_prefix: bool = False,
    ) -> Result:
        """Remove a game's files and clean up legendary's bookkeeping.

        ``legendary uninstall`` cannot be trusted to delete files: its
        per-game catalog lookup can fail with HTTP 401 (expired Epic
        auth), after which it **skips the deletion but still exits 0**,
        printing "please remove <path> manually". The old code only
        checked the exit code, so it reported success while leaving the
        full install (often many GiB) on disk.

        So we resolve the install dir from legendary's *local*
        ``installed.json`` (no network), run ``legendary uninstall`` only
        as best-effort metadata cleanup, then delete the directory and
        purge the registry entry ourselves — the latter is essential
        because a leftover entry makes the next library sync re-flag the
        game installed.
        """
        # Resolve the install dir while legendary's bookkeeping is intact.
        install_path = await asyncio.to_thread(
            _read_legendary_install_path, game_id,
        )

        # Best-effort: lets legendary tidy its own manifest/metadata when
        # it's online + authed. Never fatal — we delete files ourselves.
        if self._cli_path:
            await self._best_effort_legendary_uninstall(game_id)

        removed = await self._delete_install_dir(install_path, game_id)

        # legendary leaves the installed.json row behind when it 401s;
        # drop it so the next sync doesn't resurrect the game as installed.
        await asyncio.to_thread(_purge_legendary_install_entry, game_id)

        if delete_prefix:
            await self._delete_prefix(game_id)

        self._library.invalidate_installed_cache()
        await self._bus.emit(
            Events.GAME_UNINSTALLED,
            store="epic",
            game_id=game_id,
        )
        if not removed:
            return Result(
                success=False,
                error="uninstall_incomplete_files_remain",
            )
        return Result(success=True)

    async def _best_effort_legendary_uninstall(self, game_id: str) -> None:
        """Run ``legendary uninstall`` without trusting its outcome."""
        if not self._cli_path:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path,
                "uninstall",
                game_id,
                "--yes",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            logger.warning("[EpicUninstall] could not spawn legendary: %s", e)
            return
        try:
            await asyncio.wait_for(
                proc.communicate(), timeout=self._uninstall_timeout,
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            logger.warning("[EpicUninstall] legendary uninstall timed out")

    async def _delete_install_dir(
        self, install_path: str | None, game_id: str,
    ) -> bool:
        """``rmtree`` the install dir. Returns True if it's gone after."""
        if not install_path:
            logger.warning(
                "[EpicUninstall] no tracked install path for %s; "
                "nothing to delete", game_id,
            )
            return True
        p = Path(install_path)
        if not await asyncio.to_thread(p.exists):
            return True
        if not _is_safe_to_delete(p):
            logger.error(
                "[EpicUninstall] refusing to delete unsafe path %s", p,
            )
            return False
        try:
            await asyncio.to_thread(shutil.rmtree, p, ignore_errors=False)
        except OSError as e:
            logger.warning("[EpicUninstall] rmtree %s failed: %s", p, e)
        gone = not await asyncio.to_thread(p.exists)
        logger.info("[EpicUninstall] deleted %s (gone=%s)", p, gone)
        return gone

    async def _delete_prefix(self, game_id: str) -> None:
        """Remove the game's Proton prefix (``delete_prefix`` path)."""
        prefix = (
            Path("~/.local/share/unifideck/prefixes").expanduser() / game_id
        )
        if not await asyncio.to_thread(prefix.exists):
            return
        if not _is_safe_to_delete(prefix):
            logger.error(
                "[EpicUninstall] refusing to delete unsafe prefix %s", prefix,
            )
            return
        await asyncio.to_thread(shutil.rmtree, prefix, ignore_errors=True)
        logger.info("[EpicUninstall] deleted prefix %s", prefix)
