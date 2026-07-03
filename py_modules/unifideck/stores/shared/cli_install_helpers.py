from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import re
    from collections.abc import Awaitable, Callable
    LineHandler = Callable[
        [str, str, "ProgressCallback | None"], Awaitable[None],
    ]
    ProgressCallback = Callable[[float | dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)
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
        proc.kill()
        await proc.wait()
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
