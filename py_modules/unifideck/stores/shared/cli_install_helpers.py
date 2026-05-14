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
    ProgressCallback = Callable[[float], Awaitable[None]]
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
