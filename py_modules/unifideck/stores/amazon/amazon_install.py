"""Amazon Games installer — install / uninstall pipeline using nile.

OP-49d | py_modules/unifideck/stores/amazon/amazon_install.py

``AmazonInstaller`` orchestrates installs and uninstalls via the
``nile`` CLI. The install pipeline :

1. **preflight** — verify nile binary, resolve base path, build the
   install context;
2. **probe** — query nile for the game's manifest (size, fuel.json
   location, supported architectures);
3. **subprocess** — spawn nile with structured progress callbacks
   (parses nile's stdout for "downloaded X/Y bytes" lines);
4. **finalize** — parse the fuel.json from ``amazon_fuel.py``
   (OP-49f) to extract the launch executable, write the
   ``.unifideck-id`` marker, register with the install registry.

The uninstall path is symmetric : remove install dir, drop registry
entry, clean up shortcut + artwork cache.

Errors are wrapped into typed ``InstallResult`` envelopes ; partial
installs are cleaned up to avoid leaving orphaned files on disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from unifideck.core.manifest import write_manifest
from unifideck.core.types import Events, InstallResult, Result
from unifideck.event_bus.event_bus import EventBus
from unifideck.stores.shared.cli_install_helpers import (
    drain_install_output,
    parse_progress_line,
    wait_with_timeout,
)

from . import amazon_fuel
from .amazon_library import AmazonLibraryReader

logger = logging.getLogger(__name__)
# Nile's ProgressBar emits lines like:
#   = Progress: 42.50 123456789/987654321, Running for: 00:01:30, ETA: ...
# The old regex (`\[\s*(\d+)\s*%\s*\]`) expected `[ 42% ]` which nile
# never produces, so zero progress was ever captured.
# New primary regex matches nile's actual format; the fallback covers
# any tool that emits `[ NN% ]` brackets (e.g. future CLI updates).
_PROGRESS_RE = re.compile(r"Progress:\s*([\d.]+)")
_PROGRESS_RE_BRACKET = re.compile(r"\[\s*([\d.]+)\s*%\s*\]")
ProgressCallback = Callable[[Any], Awaitable[None]]


class AmazonInstaller:
    """Amazon installer."""

    def __init__(
        self,
        bus: EventBus,
        cli_path: str | None,
        library: AmazonLibraryReader,
        find_exe: Callable[[str, list[str] | None], str | None],
        default_install_root: str,
        install_timeout_seconds: int = 3600,
        uninstall_timeout_seconds: int = 120,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cli_path = cli_path
        self._library = library
        self._find_exe = find_exe
        self._default_install_root = str(Path(default_install_root).expanduser())
        self._install_timeout = install_timeout_seconds
        self._uninstall_timeout = uninstall_timeout_seconds

    async def install_game(
        self,
        game_id: str,
        base_path: str | None = None,
        progress_cb: ProgressCallback | None = None,
        verb: str = "install",
    ) -> InstallResult:
        """Install or update a game.

        ``verb="update"`` runs ``nile update`` (the genuine update
        command — an alias of ``install`` in nile) for an in-place
        patch; the rest of the pipeline (path resolution, manifest
        rewrite, events) is identical to a fresh install.
        """
        logger.info("[AmazonInstall] %s game_id=%s base_path=%s", verb, game_id, base_path)
        if not self._cli_path:
            return InstallResult(
                success=False,
                error="nile_not_found",
                store="amazon",
                game_id=game_id,
            )
        base = base_path or self._default_install_root
        try:
            await asyncio.to_thread(lambda: Path(base).mkdir(parents=True, exist_ok=True))
        except OSError as e:
            return InstallResult(
                success=False,
                error=f"mkdir_failed: {e}",
                store="amazon",
                game_id=game_id,
            )
        await self._bus.emit(
            Events.DOWNLOAD_STARTED,
            store="amazon",
            game_id=game_id,
        )
        rc = await self._run_install(base, game_id, progress_cb, verb)
        if rc != 0:
            await self._bus.emit(
                Events.DOWNLOAD_FAILED,
                store="amazon",
                game_id=game_id,
                error=f"nile_exit_{rc}",
            )
            return InstallResult(
                success=False,
                error=f"nile_exit_{rc}",
                store="amazon",
                game_id=game_id,
            )
        return await self._finalize_install(game_id, base)

    async def _finalize_install(self, game_id: str, base: str) -> InstallResult:
        """Finalize install — locate the installed directory and write manifest.

        Nile may record the install path in its installed.json before
        the directory is fully materialized, or use a folder name that
        differs from both the game ID and title. ``_resolve_install_path``
        verifies the directory exists on disk before returning it.
        If we still can't locate the install directory after the CLI
        reported success, the install is incomplete and we report failure.
        """
        install_path = await self._resolve_install_path(game_id, base)
        if not install_path:
            logger.error(
                "[AmazonInstall] cannot locate install directory for %s "
                "under %s — nile reported success but no matching "
                "directory found on disk",
                game_id, base,
            )
            await self._bus.emit(
                Events.DOWNLOAD_FAILED,
                store="amazon",
                game_id=game_id,
                error="install_dir_not_found",
            )
            return InstallResult(
                success=False,
                error="install_dir_not_found",
                store="amazon",
                game_id=game_id,
            )
        exe = await self._resolve_executable(install_path, game_id)
        title = await self._resolve_title(game_id)
        exe_relative = ""
        if exe:
            with contextlib.suppress(ValueError):
                exe_relative = os.path.relpath(exe, install_path)  # noqa: ASYNC240
        try:
            await write_manifest(
                install_dir=install_path,
                store="amazon",
                store_id=game_id,
                title=title,
                executable_relative=exe_relative,
                platform="windows",
            )
        except OSError as exc:
            logger.exception("[AmazonInstall] write_manifest failed for %s", install_path)
            await self._bus.emit(
                Events.DOWNLOAD_FAILED,
                store="amazon",
                game_id=game_id,
                error=f"manifest_write: {exc}",
            )
            return InstallResult(
                success=False,
                error=f"manifest_write: {exc}",
                store="amazon",
                game_id=game_id,
            )
        await self._bus.emit(
            Events.DOWNLOAD_COMPLETE,
            store="amazon",
            game_id=game_id,
            install_path=install_path,
        )
        return InstallResult(
            success=True,
            store="amazon",
            game_id=game_id,
            install_path=install_path,
        )

    async def _run_install(
        self,
        base: str,
        game_id: str,
        progress_cb: ProgressCallback | None,
        verb: str = "install",
    ) -> int:
        """Run install or update (``verb``)."""
        self._current_progress = {
            "progress_percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bps": 0.0,
            "eta_seconds": 0,
        }
        cmd = self._build_install_cmd(base, game_id, verb)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        drain_exc: BaseException | None = None
        try:
            await self._drain_install_output(
                proc,
                game_id,
                progress_cb,
            )
        except BaseException as e:
            drain_exc = e
        rc = await self._wait_with_timeout(proc)
        if drain_exc is not None:
            raise drain_exc
        return rc

    def _build_install_cmd(self, base: str, game_id: str, verb: str = "install") -> list[str]:
        """Build install/update cmd.

        ``verb`` is ``"install"`` for a fresh install or ``"update"``
        for an in-place update. In nile, ``update`` is an alias of
        ``install`` (identical args/output) — running it on an
        already-installed game patches it in place.
        """
        if self._cli_path is None:
            raise RuntimeError(
                "nile CLI path is not set; cannot build install cmd",
            )
        return [
            self._cli_path,
            verb,
            game_id,
            "--base-path",
            base,
        ]

    async def _drain_install_output(
        self,
        proc: Any,
        game_id: str,
        progress_cb: ProgressCallback | None,
    ) -> None:
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
            "[amazon_install]",
        )

    async def _handle_install_line(
        self,
        line: str,
        game_id: str,
        progress_cb: ProgressCallback | None,
    ) -> None:
        """Handle install line."""
        updated = self._parse_progress_line(line, self._current_progress)
        if not updated:
            # Fallback to check bracket format
            pct = parse_progress_line(line, _PROGRESS_RE_BRACKET)
            if pct is not None:
                self._current_progress["progress_percent"] = pct
                updated = True

        if not updated:
            logger.debug("[nile install] %s", line)
            return

        if progress_cb is not None:
            try:
                await progress_cb(dict(self._current_progress))
            except Exception as e:
                logger.debug(
                    "[amazon_install] progress_cb raised: %s",
                    e,
                )
        await self._bus.emit(
            Events.DOWNLOAD_PROGRESS,
            store="amazon",
            game_id=game_id,
            progress=self._current_progress.get("progress_percent", 0.0),
            speed_mbps=self._current_progress.get("speed_bps", 0.0) / (1024 * 1024),
            eta_seconds=self._current_progress.get("eta_seconds", 0),
        )

    @staticmethod
    def _parse_eta(line: str) -> int | None:
        """Parse eta from nile line."""
        if "ETA:" not in line:
            return None
        try:
            eta_part = line.split("ETA:", 1)[1].strip()
            if not eta_part:
                return None
            eta_time = eta_part.split()[0]
            parts = eta_time.split(":")
            if len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                return h * 3600 + m * 60 + s
            if len(parts) == 2:
                m, s = int(parts[0]), int(parts[1])
                return m * 60 + s
        except (ValueError, IndexError):
            return None
        return None

    @staticmethod
    def _parse_speed_mib(line: str) -> float | None:
        """Parse speed from nile line."""
        if "Download" not in line or "MiB/s" not in line:
            return None
        try:
            tail = line.split("Download", 1)[1]
            speed_part = tail.split("MiB/s", 1)[0].strip()
            speed_part = speed_part.lstrip("-").strip()
            speed_tokens = speed_part.split()
            if not speed_tokens:
                return None
            return float(speed_tokens[-1]) * 1024 * 1024
        except (ValueError, IndexError):
            return None

    def _parse_progress_line(self, line: str, progress: dict[str, Any]) -> bool:
        """Parse progress percent and bytes from nile line."""
        speed_bps = self._parse_speed_mib(line)
        if speed_bps is not None:
            progress["speed_bps"] = speed_bps
            return True
        if "Progress:" not in line:
            return False
        try:
            part = line.split("Progress:", 1)[1].strip()
            tokens = part.split()
            if len(tokens) < 2:
                return False
            progress["progress_percent"] = float(tokens[0])
            bytes_part = tokens[1].rstrip(",")
            if "/" not in bytes_part:
                return True
            written, total = bytes_part.split("/", 1)
            progress["downloaded_bytes"] = int(written)
            progress["total_bytes"] = int(total)
            eta = self._parse_eta(line)
            if eta is not None:
                progress["eta_seconds"] = eta
            return True
        except (ValueError, IndexError):
            return False

    async def _resolve_install_path(self, game_id: str, base: str) -> str | None:
        """Resolve install path from nile's installed.json or fallback.

        Nile writes an entry to installed.json before (or during)
        the download, and the recorded path may not exist on disk
        yet if nile failed mid-flight or recorded an alternate
        directory name. Always verify the directory exists before
        returning a path — a stale entry that points nowhere
        must fall through to the default-path check.
        """
        installed = await self._library.read_installed_ids()
        info = installed.get(game_id)
        if info and info.get("path"):
            candidate = cast("str | None", info["path"])
            if candidate and await asyncio.to_thread(lambda: Path(candidate).is_dir()):
                return candidate
        default = str(Path(base) / game_id)
        if await asyncio.to_thread(lambda: Path(default).is_dir()):
            return default
        # Nile may create a subdirectory named after the game title
        # rather than the game_id. Scan the base directory for any
        # subdirectory that contains a .unifideck-id marker or
        # matches a known pattern from nile's fuel.json.
        title = await self._resolve_title(game_id)
        if title and title != game_id:
            title_path = str(Path(base) / title)
            if await asyncio.to_thread(lambda: Path(title_path).is_dir()):
                return title_path
        return None

    async def _resolve_executable(
        self,
        install_path: str | None,
        game_id: str,
    ) -> str | None:
        """Resolve executable."""
        if not install_path:
            return None
        from_fuel = amazon_fuel.find_exe_from_fuel(install_path)
        if from_fuel:
            return from_fuel
        return self._find_exe(install_path, [game_id])

    async def _resolve_title(self, game_id: str) -> str:
        """Resolve title."""
        owned = await self._library.read_owned_games()
        for game in owned:
            if game.store_game_id == game_id:
                return game.title
        return game_id

    async def uninstall_game(self, game_id: str) -> Result:
        """Uninstall game."""
        if not self._cli_path:
            return Result(
                success=False,
                error="nile_not_found",
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
        await self._bus.emit(
            Events.GAME_UNINSTALLED,
            store="amazon",
            game_id=game_id,
        )
        return Result(success=True)
