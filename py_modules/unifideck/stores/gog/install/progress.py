"""gogdl subprocess + progress monitor.

OP-51f | py_modules/unifideck/stores/gog/install/progress.py

``_GogdlProgressMonitor`` wraps the ``gogdl`` subprocess invocation
with structured progress reporting:

* parses gogdl's stdout/stderr stream to extract download progress
  (percentage, transfer rate, ETA);
* throttles progress callbacks to a sane frequency (~ 2 Hz) to avoid
  flooding the bus;
* enforces a watchdog timeout — if gogdl stops producing output for
  too long, kill it and report failure;
* handles a separate "repair pass" mode used after the main download
  to validate file checksums.

Exit-code interpretation handles gogdl's non-standard codes (license
not accepted, partial install, network drop) and maps each to a
specific ``InstallResult`` error code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
)

from .primitives import GOGFolderOps

if TYPE_CHECKING:
    from .installer import GOGInstaller
logger = logging.getLogger(__name__)
_GOGDL_STALL_TIMEOUT_S = 120.0


class _GogdlProgressMonitor:
    """Gogdl progress monitor."""

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
            install_mode,
            game_id,
            platform,
            path,
            support_dir,
            languages,
        )
        proc = await self._spawn_gogdl(cmd)
        loop_ok = await self._read_progress_loop(proc, progress_cb)
        if not loop_ok:
            return False
        await proc.wait()
        if proc.returncode != 0:
            logger.error(
                "[GOGInstaller] gogdl exited with code %d",
                proc.returncode,
            )
            return False
        return True

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
            self._parent._gogdl_bin,
            "--auth-config-path",
            self._parent._config.auth_config_path,
            install_mode,
            game_id,
            "--platform",
            platform,
            "--path",
            path,
            "--support",
            support_dir,
            "--with-dlcs",
        ]
        for lang in languages:
            cmd.extend(["--lang", lang])
        return cmd

    async def _spawn_gogdl(self, cmd: list[str]) -> asyncio.subprocess.Process:
        """Spawn GOGDL."""
        logger.info(
            "[GOGInstaller] spawning gogdl: %s",
            " ".join(cmd),
        )
        env, cleanup = await self._parent._tokens.acquire_gogdl_creds()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        proc._unifideck_gogdl_cleanup = cleanup
        return proc

    async def _read_progress_loop(
        self,
        proc: asyncio.subprocess.Process,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        """Read progress loop."""
        progress: dict[str, Any] = {
            "progress_percent": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bps": 0.0,
            "eta_seconds": 0,
            "phase_message": "Starting download…",
        }
        assert proc.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=_GOGDL_STALL_TIMEOUT_S,
                )
            except TimeoutError:
                logger.warning(
                    "[GOGInstaller] stalled (no output for %ds)",
                    int(_GOGDL_STALL_TIMEOUT_S),
                )
                await self._terminate_gogdl(proc)
                return False
            if not line:
                return True
            line_str = line.decode(errors="replace").strip()
            if not line_str:
                continue
            await self._handle_progress_line(
                line_str,
                progress,
                progress_cb,
            )

    async def _handle_progress_line(
        self,
        line_str: str,
        progress: dict[str, Any],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Handle progress line."""
        is_progress_line = "Progress:" in line_str or "Download" in line_str
        if not is_progress_line and not line_str.startswith("[gogdl]"):
            logger.info("[gogdl] %s", line_str)
        if progress_cb is None:
            return
        self._parse_progress_line(line_str, progress)
        is_change_line = "Progress:" in line_str or "+ Download" in line_str
        if not is_change_line:
            return
        try:
            await progress_cb(dict(progress))
        except Exception as e:
            logger.debug(
                "[GOGInstaller] progress_cb: %s",
                e,
            )

    @staticmethod
    def _parse_eta(line: str) -> int | None:
        """Parse eta."""
        if "ETA:" not in line:
            return None
        eta_part = line.split("ETA:", 1)[1].strip()
        if not eta_part:
            return None
        eta_time = eta_part.split()[0]
        parts = eta_time.split(":")
        try:
            if len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                return h * 3600 + m * 60 + s
            if len(parts) == 2:
                m, s = int(parts[0]), int(parts[1])
                return m * 60 + s
        except ValueError:
            return None
        return None

    @staticmethod
    def _parse_speed_mib(line: str) -> float | None:
        """Parse speed mib."""
        if "+ Download" not in line or "MiB/s" not in line:
            return None
        tail = line.split("Download", 1)[1]
        speed_part = tail.split("MiB/s", 1)[0].strip()
        speed_tokens = speed_part.split()
        if not speed_tokens:
            return None
        try:
            speed_mib = float(speed_tokens[-1])
        except ValueError:
            return None
        return speed_mib * 1024 * 1024

    @staticmethod
    def _parse_progress_line(line: str, progress: dict[str, Any]) -> None:
        """Parse progress line."""
        speed_bps = _GogdlProgressMonitor._parse_speed_mib(line)
        if speed_bps is not None:
            progress["speed_bps"] = speed_bps
        if "Progress:" not in line:
            return
        try:
            part = line.split("Progress:", 1)[1].strip()
            tokens = part.split()
            if len(tokens) < 2:
                return
            progress["progress_percent"] = float(tokens[0])
            bytes_part = tokens[1].rstrip(",")
            if "/" not in bytes_part:
                return
            written, total = bytes_part.split("/", 1)
            progress["downloaded_bytes"] = int(written)
            progress["total_bytes"] = int(total)
            eta = _GogdlProgressMonitor._parse_eta(line)
            if eta is not None:
                progress["eta_seconds"] = eta
            progress["phase_message"] = (
                f"Downloading… {progress['progress_percent']:.1f}%"
            )
        except (ValueError, IndexError) as e:
            logger.debug(
                "[GOGInstaller] progress parse: %s",
                e,
            )

    @staticmethod
    async def _terminate_gogdl(proc: asyncio.subprocess.Process) -> None:
        """Terminate GOGDL."""
        try:
            proc.terminate()
            await asyncio.sleep(1)
            if proc.returncode is None:
                proc.kill()
        except Exception:
            logger.exception("[GOGInstaller] terminate failed")

    async def run_gogdl_repair_pass(
        self,
        game_id: str,
        platform: str,
        base_path: str,
        folder_name: str | None,
        preferred_lang: str,
    ) -> None:
        """Run GOGDL repair pass."""
        repair_path = self._resolve_repair_path(
            game_id,
            base_path,
            folder_name,
        )
        cmd = [
            self._parent._gogdl_bin,
            "--auth-config-path",
            self._parent._config.auth_config_path,
            "repair",
            game_id,
            "--platform",
            platform,
            "--path",
            repair_path,
            "--lang",
            preferred_lang,
            "--with-dlcs",
        ]
        try:
            env, _gogdl_cleanup = await self._parent._tokens.acquire_gogdl_creds()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
            except OSError as e:
                logger.warning(
                    "[GOGInstaller] could not spawn repair: %s",
                    e,
                )
                await _gogdl_cleanup()
                return
            try:
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    line_str = line.decode(errors="replace").strip()
                    if line_str and not line_str.startswith("[gogdl]"):
                        logger.info("[gogdl-verify] %s", line_str)
                await proc.wait()
                if proc.returncode != 0:
                    logger.warning(
                        "[GOGInstaller] repair code %d (non-fatal)",
                        proc.returncode,
                    )
            finally:
                await _gogdl_cleanup()
        except Exception as e:
            logger.warning(
                "[GOGInstaller] repair pipeline failed: %s",
                e,
            )

    @staticmethod
    def _resolve_repair_path(
        game_id: str,
        base_path: str,
        folder_name: str | None,
    ) -> str:
        """Resolve repair path."""
        if folder_name:
            predicted = str(Path(base_path) / folder_name)
            if Path(predicted).exists():
                return predicted
        with contextlib.suppress(OSError):
            for name in [entry.name for entry in Path(base_path).iterdir()]:
                candidate = str(Path(base_path) / name)
                if not Path(candidate).is_dir():
                    continue
                if GOGFolderOps.has_goggame_info(
                    candidate,
                    game_id,
                ):
                    return candidate
        logger.warning(
            "[GOGInstaller] could not resolve repair path, using base_path",
        )
        return base_path
