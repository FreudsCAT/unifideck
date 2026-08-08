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
# Stall watchdog for the *active download* phase. gogdl's progressbar emits a
# "+ Disk … (write)/(read)" heartbeat roughly every second for the whole
# download (even at 0 MiB/s while it retries the CDN), so any line resets this
# — 2 min only fires on a genuine no-output stall.
_GOGDL_STALL_TIMEOUT_S = 120.0
# Tolerated silence once bytes are complete (~100%): gogdl may go quiet during
# native archive extraction / worker shutdown / manifest write before EOF, and
# the bounded post-EOF wait covers a process that closes stdout then lingers.
# NOTE: no absolute wall-clock cap on the tail — a legitimately slow CDN can
# keep a download at ~100% for a long time, and killing that would fail a
# working install. The per-read window is the only bound; the heartbeat keeps
# it from firing on a live-but-slow download.
_GOGDL_FINALIZE_TIMEOUT_S = 1800.0
# Tolerated silence for the conditional repair pass. gogdl `repair` re-hashes
# every file as ONE silent block (~11 min for ~53 GB on microSD in the field
# logs, scales with game size / disk speed). Repair must be allowed to finish,
# so this is deliberately generous — it only guards a truly wedged process.
_GOGDL_REPAIR_TIMEOUT_S = 3600.0
# Progress threshold at which the download is effectively done and we flip the
# UI to the indeterminate "Extracting…" phase so the row stops looking frozen.
_GOGDL_TAIL_PROGRESS_PCT = 99.0


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
        env, creds_path, cleanup = await self._parent._tokens.acquire_gogdl_creds()
        try:
            cmd = self._build_gogdl_cmd(
                creds_path,
                install_mode,
                game_id,
                platform,
                path,
                support_dir,
                languages,
            )
            proc = await self._spawn_gogdl(cmd, env)
            loop_ok = await self._read_progress_loop(proc, progress_cb)
            if not loop_ok:
                return False
            # gogdl closed stdout (EOF) but may still be flushing/exiting.
            # Bound the wait so a process that closes stdout then hangs can't
            # wedge the queue forever; the read loop already tolerated the
            # silent extraction tail via the finalize timeout.
            try:
                await asyncio.wait_for(proc.wait(), timeout=_GOGDL_FINALIZE_TIMEOUT_S)
            except TimeoutError:
                logger.warning(
                    "[GOGInstaller] gogdl did not exit after EOF (%ds) — killing",
                    int(_GOGDL_FINALIZE_TIMEOUT_S),
                )
                await self._terminate_gogdl(proc)
                return False
            if proc.returncode != 0:
                logger.error(
                    "[GOGInstaller] gogdl exited with code %d",
                    proc.returncode,
                )
                return False
            return True
        finally:
            await cleanup()

    def _build_gogdl_cmd(
        self,
        creds_path: str,
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
            creds_path,
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

    async def _spawn_gogdl(
        self,
        cmd: list[str],
        env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Spawn GOGDL."""
        logger.info(
            "[GOGInstaller] spawning gogdl: %s",
            " ".join(cmd),
        )
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

    async def _read_progress_loop(
        self,
        proc: asyncio.subprocess.Process,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        """Read progress loop.

        Two-phase watchdog: while bytes are still downloading a stall is a hard
        failure (tight ``_GOGDL_STALL_TIMEOUT_S``). Once progress crosses
        ``_GOGDL_TAIL_PROGRESS_PCT`` the download is effectively done; gogdl can
        go quiet during finalization, so we widen the per-read window to
        ``_GOGDL_FINALIZE_TIMEOUT_S`` and flip the UI to the indeterminate
        "Extracting…" phase so the row stops looking frozen at 100%. Any line
        (including gogdl's ~1 Hz heartbeat) resets the window, so a live-but-slow
        download is never killed.
        """
        progress: dict[str, Any] = {
            "progress_percent": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bps": 0.0,
            "eta_seconds": 0,
            "phase_message": "Starting download…",
        }
        assert proc.stdout is not None
        in_tail = False
        while True:
            in_tail = await self._maybe_enter_tail(
                progress,
                progress_cb,
                in_tail=in_tail,
            )
            timeout = _GOGDL_FINALIZE_TIMEOUT_S if in_tail else _GOGDL_STALL_TIMEOUT_S
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=timeout,
                )
            except TimeoutError:
                logger.warning(
                    "[GOGInstaller] stalled (no output for %ds, tail=%s)",
                    int(timeout),
                    in_tail,
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

    async def _maybe_enter_tail(
        self,
        progress: dict[str, Any],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
        *,
        in_tail: bool,
    ) -> bool:
        """Flip into the finalization tail once download bytes are complete.

        Emits a single ``phase="extracting"`` callback so the UI switches to
        the indeterminate "Extracting…" spinner. Idempotent — returns ``True``
        unchanged once already in the tail.
        """
        if in_tail:
            return True
        if float(progress.get("progress_percent") or 0) < _GOGDL_TAIL_PROGRESS_PCT:
            return False
        # Stamp the shared dict so the phase stays "extracting" for any later
        # callbacks too — the indeterminate spinner shouldn't flip back.
        progress["phase"] = "extracting"
        if progress_cb is not None:
            try:
                await progress_cb({**progress, "phase_message": "Extracting…"})
            except Exception as e:
                logger.debug("[GOGInstaller] extracting phase_cb: %s", e)
        logger.info("[GOGInstaller] download bytes complete → extracting/finalizing")
        return True

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
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Run GOGDL repair pass.

        ``repair`` re-reads every file and re-hashes it against the manifest,
        re-downloading mismatches — a full read-back over the whole game. It is
        run *conditionally* (only when a download came up short), so when it
        does run we surface it as an indeterminate "Verifying…" phase with live
        percent text rather than leaving the row frozen at 100%.
        """
        repair_path = self._resolve_repair_path(
            game_id,
            base_path,
            folder_name,
        )
        try:
            env, creds_path, _gogdl_cleanup = await self._parent._tokens.acquire_gogdl_creds()
            cmd = [
                self._parent._gogdl_bin,
                "--auth-config-path",
                creds_path,
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
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
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
                await self._read_repair_loop(proc, progress_cb)
                try:
                    await asyncio.wait_for(
                        proc.wait(), timeout=_GOGDL_REPAIR_TIMEOUT_S,
                    )
                except TimeoutError:
                    logger.warning(
                        "[GOGInstaller] repair did not exit (%ds) — killing",
                        int(_GOGDL_REPAIR_TIMEOUT_S),
                    )
                    await self._terminate_gogdl(proc)
                if proc.returncode not in (0, None):
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

    async def _read_repair_loop(
        self,
        proc: asyncio.subprocess.Process,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Drain repair stdout, reporting a "Verifying…" phase.

        Reuses ``_parse_progress_line`` (repair emits the same ``Progress:``
        format as download). Guarded by the finalize-phase watchdog so a wedged
        repair can't read forever. Returns on EOF or watchdog kill — the caller
        bounds ``proc.wait()`` and inspects the return code.
        """
        assert proc.stdout is not None
        progress: dict[str, Any] = {
            "progress_percent": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bps": 0.0,
            "eta_seconds": 0,
            "phase": "verifying",
            "phase_message": "Verifying…",
        }
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=_GOGDL_REPAIR_TIMEOUT_S,
                )
            except TimeoutError:
                logger.warning(
                    "[GOGInstaller] repair stalled (no output for %ds) — killing",
                    int(_GOGDL_REPAIR_TIMEOUT_S),
                )
                await self._terminate_gogdl(proc)
                return
            if not line:
                return
            line_str = line.decode(errors="replace").strip()
            if not line_str:
                continue
            if not line_str.startswith("[gogdl]"):
                logger.info("[gogdl-verify] %s", line_str)
            if progress_cb is None or "Progress:" not in line_str:
                continue
            self._parse_progress_line(line_str, progress)
            pct = float(progress.get("progress_percent") or 0)
            progress["phase"] = "verifying"
            progress["phase_message"] = f"Verifying… {pct:.1f}%"
            try:
                await progress_cb(dict(progress))
            except Exception as e:
                logger.debug("[GOGInstaller] verify phase_cb: %s", e)

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
