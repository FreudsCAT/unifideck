"""auth/edge_browser/process_ops.py — Edge process lifecycle helpers.

Extracted on 2026-04-20 from ``edge.py`` to keep ``EdgeBrowser``
as a pure façade. The two functions here handle the only
real logic left in ``EdgeBrowser`` after the 4-sub-component
decomposition (CDP client, installer, profile manager, launch):

 - ``graceful_kill(proc)`` — terminate with SIGTERM/SIGKILL,
   preferring the process group when available so Chromium
   renderer children get reaped too, falling back to the
   single-process path on platforms without pgid support.
 - ``wait_and_check_crash(proc, probe_cdp)`` — poll at 500 ms
   for up to 10 s: return False if the process exited during
   startup (crash), True when CDP responds or the timeout is
   hit with the process still alive.

Both are pure in the sense of taking the process/probe as
explicit arguments — no ``self``, no hidden state on the
class, so they test cleanly with a subprocess stub.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_TERM_TIMEOUT_S = 10
_KILL_TIMEOUT_S = 3
_COOKIE_FLUSH_DELAY_S = 1
_STARTUP_POLL_STEPS = 20
_STARTUP_POLL_INTERVAL_S = 0.5
_CRASH_LOG_TAIL_CHARS = 300


def graceful_kill(proc: subprocess.Popen[bytes] | None) -> None:
    """Stop the Edge auth browser cleanly, SIGKILL on timeout.

    Waits 1 s before SIGTERM so cookies can flush to disk, then
    escalates to SIGKILL if the process hasn't exited after 10 s.

    Does NOT ``pkill`` by profile name — that would kill game
    sessions launched by the launcher sharing the same profile.
    Does not touch the profile state — callers are expected to
    call ``cleanup_stale_profile_state`` themselves if needed.
    """
    if proc is None:
        return
    try:
        import time
        time.sleep(_COOKIE_FLUSH_DELAY_S)
        _signal_group_or_single(proc, signal.SIGTERM)
        proc.wait(timeout=_TERM_TIMEOUT_S)
        logger.info("[Edge] Auth browser closed (cookies flushed)")
    except subprocess.TimeoutExpired:
        _force_kill(proc)
    except Exception as e:
        logger.debug("[Edge] Auth browser kill error (non-fatal): %s", e)


def _signal_group_or_single(
    proc: subprocess.Popen[bytes], sig: int,
) -> None:
    """Send ``sig`` to the process group if one exists, else to PID.

    Chromium spawns renderer subprocesses in its own process
    group; signalling the group reaps them atomically. On the
    rare systems where ``getpgid`` fails (permission, namespaces,
    race with reaping), we fall back to signalling the leader
    only — some renderers may linger but will be cleaned up by
    the init process.
    """
    pgid = _safe_getpgid(proc.pid)
    if pgid is not None and pgid != os.getpgrp():
        os.killpg(pgid, sig)
    elif sig == signal.SIGTERM:
        proc.terminate()
    else:
        proc.kill()


def _safe_getpgid(pid: int) -> int | None:
    """Return ``os.getpgid(pid)`` or None on any error.

    Raced reap, permission denied, and missing-pid all degrade
    to a None return — callers interpret that as "fall back to
    single-process signalling".
    """
    try:
        return os.getpgid(pid)
    except Exception:
        # process already reaped, no permission, namespace quirk
        return None


def _force_kill(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL escalation when SIGTERM didn't stop the process.

    Best-effort: if the kill itself fails or the post-wait
    times out again, we log and move on — at that point the
    process is either stuck in uninterruptible sleep (kernel
    bug territory) or already gone.
    """
    logger.debug("[Edge] Auth browser didn't exit -- sending SIGKILL")
    try:
        _signal_group_or_single(proc, signal.SIGKILL)
        proc.wait(timeout=_KILL_TIMEOUT_S)
    except Exception as e:
        # Process may be stuck in D state, or we may have lost
        # the race against natural exit; nothing more to do.
        logger.debug("[Edge] SIGKILL/wait failed: %s", e)


async def wait_and_check_crash(
    proc: subprocess.Popen[bytes] | None,
    probe_cdp: Callable[[], bool],
    log_file: str,
) -> bool:
    """Poll for Edge startup, return False if the process crashed.

    Called at the start of the auth monitor task. Polls every
    500 ms for up to 10 s to allow the browser time to start on
    loaded systems.

    Returns:
        True if CDP became responsive, or the process is still
            alive after the full polling window (let the caller
            retry CDP); False if the process exited during
            startup (crash).

    ``probe_cdp`` is invoked in a thread (blocking TCP probe is
    expected) and must return True when the CDP WebSocket
    endpoint is reachable. ``log_file`` is peeked on crash to
    surface the last stderr in the warning log.
    """
    if proc is None:
        return False
    for _ in range(_STARTUP_POLL_STEPS):
        await asyncio.sleep(_STARTUP_POLL_INTERVAL_S)
        if proc.poll() is not None:
            _log_crash_tail(log_file)
            return False
        if await asyncio.to_thread(probe_cdp):
            return True
    logger.warning(
        "[Edge] Auth browser started but CDP port not "
        "responding after %ds",
        int(_STARTUP_POLL_STEPS * _STARTUP_POLL_INTERVAL_S),
    )
    return True  # process is alive, let caller retry CDP


def _log_crash_tail(log_file: str) -> None:
    """Best-effort read of the Edge log after a startup crash."""
    err = ""
    try:
        with Path(log_file).open() as f:
            err = f.read()[:_CRASH_LOG_TAIL_CHARS]
    except Exception as e:
        # Log file missing / unreadable on Edge profile teardown.
        # We still want to report the crash even without a tail.
        logger.debug("[Edge] crash log read failed: %s", e)
    logger.error(
        "[Edge] Auth browser crashed before CDP. stderr: %s", err,
    )
