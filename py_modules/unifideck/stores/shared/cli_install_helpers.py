from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import re
    from collections.abc import Awaitable, Callable
    LineHandler = Callable[
        [str, str, "ProgressCallback | None"], Awaitable[None],
    ]
    ProgressCallback = Callable[[float | dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)


class TailRingBuffer:
    """Bounded FIFO of a CLI's most-recent non-progress output lines.

    A store installer streams a download tool's stdout line-by-line and
    forwards only the progress/speed lines to the UI; the *other* lines
    (which is where the tool prints its actual error, e.g. legendary's
    ``[cli] ERROR: …``) would otherwise be dropped. Feed those lines to
    :meth:`append` and, on a non-zero exit, call :meth:`tail` to recover
    the last few for the failure message. One instance per install (bind
    it via ``functools.partial`` into the line handler) so concurrent or
    sequential installs never share state.
    """

    def __init__(self, maxlen: int = 20) -> None:
        """Keep at most ``maxlen`` lines (the newest win)."""
        self._lines: deque[str] = deque(maxlen=maxlen)

    def append(self, line: str) -> None:
        """Record one output line (empty lines are ignored)."""
        if line:
            self._lines.append(line)

    def tail(self, count: int = 5, sep: str = " | ") -> str:
        """Return the last ``count`` recorded lines joined by ``sep``."""
        if not self._lines:
            return ""
        recent = list(self._lines)[-count:]
        return sep.join(recent)
async def drain_install_output(
    proc: Any,
    game_id: str,
    progress_cb: ProgressCallback | None,
    line_handler: LineHandler,
) -> None:
    """Drain install output."""
    assert proc.stdout is not None
    while True:
        line_bytes = await proc.stdout.readline()
        if not line_bytes:
            break
        line = line_bytes.decode(errors="ignore").strip()
        if line:
            await line_handler(line, game_id, progress_cb)
def _child_pids(pid: int) -> list[int]:
    """Direct children of ``pid``, from procfs. Empty if unreadable."""
    kids: list[int] = []
    task_dir = Path(f"/proc/{pid}/task")
    try:
        for tid in task_dir.iterdir():
            try:
                raw = (tid / "children").read_text()
            except OSError:
                continue
            kids.extend(int(p) for p in raw.split())
    except OSError:
        return []
    return kids


def _process_tree(pid: int) -> list[int]:
    """``pid`` plus every descendant, deepest last.

    Used to signal a download tool's whole tree. A process-group kill
    would be simpler but is NOT safe here: these children are spawned
    without a new session, so they share the plugin host's process group
    — ``killpg`` would take down ``plugin_loader`` itself.
    """
    seen: list[int] = [pid]
    frontier = [pid]
    while frontier:
        nxt: list[int] = []
        for parent in frontier:
            for kid in _child_pids(parent):
                if kid not in seen:
                    seen.append(kid)
                    nxt.append(kid)
        frontier = nxt
    return seen


async def terminate_process_tree(
    proc: Any, log_prefix: str, *, grace_s: float = 5.0,
) -> None:
    """Stop ``proc`` and every descendant; SIGKILL whatever survives.

    ``proc.kill()`` alone is not enough for legendary: it drives its
    downloads through ``multiprocessing`` workers, so killing just the
    parent leaves children alive — and they inherited the
    ``installed.json.lock`` file descriptor. A surviving child therefore
    keeps legendary's install lock held, and legendary answers *every*
    later install by printing a CRITICAL and **exiting 0** — which the
    caller then reads as success. That is exactly how a cancelled
    install turned every subsequent one into an instant phantom
    "success" with nothing on disk.

    The waits swallow ``CancelledError`` on purpose. The usual caller is
    a task that is *already* being cancelled, where every ``await``
    re-raises immediately — without this the SIGKILL escalation would be
    skipped and a SIGTERM-ignoring child would survive holding the lock,
    which is the whole failure being prevented. The signals themselves
    are sent before any ``await``, so they land regardless. Callers
    propagate their own cancellation afterwards.

    Safe to call on an already-exited process.
    """
    if proc.returncode is not None:
        return
    pids = _process_tree(proc.pid)
    _signal_tree(pids, signal.SIGTERM)
    await _settle(proc, grace_s)
    survivors = [pid for pid in pids if _pid_alive(pid)]
    if survivors:
        logger.warning(
            "%s %d process(es) ignored SIGTERM, sending SIGKILL: %s",
            log_prefix, len(survivors), survivors,
        )
        _signal_tree(survivors, signal.SIGKILL)
        await _settle(proc, grace_s)
    logger.info("%s terminated process tree %s", log_prefix, pids)


def _signal_tree(pids: list[int], sig: int) -> None:
    """Signal every pid, children before parents. Never raises."""
    for pid in reversed(pids):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, sig)


async def _settle(proc: Any, grace_s: float) -> None:
    """Give a signalled process a moment to exit; never raises."""
    with contextlib.suppress(
        TimeoutError, ProcessLookupError, asyncio.CancelledError,
    ):
        await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=grace_s)


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` still exists (zombies count as gone)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    # " (name) S rest" — state is the field after the closing paren.
    fields = stat.rpartition(")")[2].split()
    return bool(fields) and fields[0] != "Z"


async def wait_with_timeout(
    proc: Any,
    timeout_s: int,
    log_prefix: str,
) -> int:
    """Wait with timeout."""
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
    except TimeoutError:
        logger.exception(
            "%s timeout after %ds, killing",
            log_prefix, timeout_s,
        )
        await terminate_process_tree(proc, log_prefix)
        return -1
    return proc.returncode or 0
def parse_progress_line(
    line: str, pattern: re.Pattern[str],
) -> float | None:
    """Parse progress line."""
    match = pattern.search(line)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None
def parse_eta_seconds(line: str) -> int | None:
    """Parse ``ETA: HH:MM:SS`` (or ``MM:SS``) from a CLI line → seconds.

    Both legendary and gogdl print ``ETA: <clock>`` on their progress
    line. Returns ``None`` when no ETA token is present or it doesn't
    parse — the caller leaves the previous value in place.
    """
    if "ETA:" not in line:
        return None
    tail = line.split("ETA:", 1)[1].strip()
    if not tail:
        return None
    parts = tail.split()[0].split(":")
    try:
        if len(parts) == 3:
            h, m, s = (int(p) for p in parts)
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            m, s = (int(p) for p in parts)
            return m * 60 + s
    except ValueError:
        return None
    return None
def parse_speed_bps(line: str) -> float | None:
    """Parse a ``+ Download … <n> MiB/s`` transfer-rate line → bytes/sec.

    Matches both gogdl (``+ Download\t+ 12.3 MiB/s``) and legendary
    (``+ Download\t- 12.3 MiB/s``) — the sign is its own token, so the
    rate is always the last token before ``MiB/s``. The ``Download``
    guard skips legendary's ``+ Disk … MiB/s`` and ``Downloaded: … MiB``
    lines (the latter has no ``/s``). Returns ``None`` on no match.
    """
    if "Download" not in line or "MiB/s" not in line:
        return None
    tokens = line.split("MiB/s", 1)[0].split()
    if not tokens:
        return None
    try:
        return float(tokens[-1]) * 1024 * 1024
    except ValueError:
        return None
