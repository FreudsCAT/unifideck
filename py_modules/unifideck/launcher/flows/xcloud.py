from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from unifideck.core.types import Result
from unifideck.launcher.cdp.xcloud_cdp import run_cdp_inject
from unifideck.launcher.types.context import LaunchContext
from unifideck.launcher.types.errors import DependencyMissingError

if TYPE_CHECKING:
    from unifideck.auth.edge_browser import EdgeBrowser
logger = logging.getLogger(__name__)
_MAX_SESSION_SECONDS = 14400
_POLL_INTERVAL_SECONDS = 5.0
_XCLOUD_CDP_PORT = 9223
_CDP_INJECT_TIMEOUT = 60.0
def _read_config_int(key: str, default: int) -> int:
    """Read config int."""
    from unifideck.utils.config_helpers import read_config_int_cold_start
    return read_config_int_cold_start(key, default)

async def launch_xcloud(
    ctx: LaunchContext,
    edge_browser: EdgeBrowser,
) -> Result:

    """Launch xcloud."""
    target_url = str(ctx.work_dir)
    logger.info(
        "[launcher.xcloud] launching: %s", target_url[:80],
    )
    if not edge_browser.is_installed:
        raise DependencyMissingError(
            "Microsoft Edge flatpak required for xCloud "
            "streaming",
            context={
                "game_id": ctx.game_id,
                "url": target_url,
            },
        )
    started = edge_browser.launch_xcloud(target_url)
    if not started:
        return Result(
            success=False,
            error="edge_launch_failed",
            store=ctx.store,
        )
    inject_task: asyncio.Task[bool] = asyncio.create_task(
        run_cdp_inject(
            port=_XCLOUD_CDP_PORT,
            launch_url=target_url,
            timeout=_CDP_INJECT_TIMEOUT,
            steam_controller_appid=int(ctx.steam_app_id or 0),
        ),
        name=f"xcloud_cdp_inject:{ctx.game_key}",
    )
    try:
        await _wait_for_session_end(edge_browser)
    finally:
        if not inject_task.done():
            inject_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inject_task
    logger.info(
        "[launcher.xcloud] session ended: %s", ctx.game_key,
    )
    return Result(success=True, store=ctx.store)
async def _wait_for_session_end(edge_browser: EdgeBrowser) -> None:
    """Wait for session end."""
    max_seconds = _read_config_int(
        "launcher.xcloud_max_seconds", _MAX_SESSION_SECONDS,
    )
    proc = edge_browser.process
    if proc is not None:
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, proc.wait),
                timeout=max_seconds,
            )
        except TimeoutError:
            logger.warning(
                "[launcher.xcloud] session reached max "
                "duration (%ds), leaving Edge running",
                max_seconds,
            )
        return
    logger.info(
        "[launcher.xcloud] no process handle, polling "
        "fallback",
    )
    elapsed = 0.0
    while elapsed < max_seconds:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS
    logger.warning(
        "[launcher.xcloud] polling fallback reached timeout",
    )
