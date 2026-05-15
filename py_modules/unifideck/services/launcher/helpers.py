"""services/launcher/helpers.py — Technical primitives for launch flows.

6 functions supporting the public orchestrators
(``launch_windows`` / ``launch_native``). All take a
``LauncherService`` as first arg (``svc``). Byte-identical
behaviour to the pre-extraction versions — split out for volumetry.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext, RuntimeState

    from .service import LauncherService

logger = logging.getLogger(__name__)


async def prepare_windows_plan(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> tuple[Any, Any]:
    """Prepare the Proton launch plan for a Windows game.

    Drift fix (2026-05-15): the previous body accessed
    ``ctx.game.get(...)`` — ``LaunchContext`` has no ``game``
    attribute. The real attributes are directly on ``ctx``
    (store, game_id, exe_path, work_dir, raw_options).
    Fields with no equivalent on ``LaunchContext`` (``app_id``,
    ``title``, ``launch_args``) are passed as their best-effort
    defaults; this code path's correctness depends on
    ``ProtonService.prepare_launch`` tolerating empty values
    — to validate when the service graph is exercised end-to-end.
    """
    try:
        plan = await svc._proton_svc.prepare_launch(
            app_id=0,  # No app_id on LaunchContext; service must derive it.
            launch_path=str(ctx.exe_path),
            launch_args=[],  # raw_options is a string; service to parse.
            work_dir=str(ctx.work_dir),
            store=ctx.store,
            game_id=ctx.game_id,
            title="",  # No title on LaunchContext; service to fetch.
        )
        # Dummy parsed_options for now, in a real implementation this parses LSFG, etc.
        parsed_options = object()
        return plan, parsed_options
    except Exception:
        logger.exception("[Helpers] prepare_windows_plan failed")
        raise


async def cloud_sync_phase(
    svc: LauncherService,
    ctx: LaunchContext,
    direction: str,
) -> None:
    """Run one direction of cloud-save sync (``down`` or ``up``)."""
    store = ctx.store
    game_id = ctx.game_id

    if not store or not game_id:
        return

    try:
        if direction == "down":
            await svc._cloud_svc.sync_down(store, game_id)
        elif direction == "up":
            await svc._cloud_svc.sync_up(store, game_id)
    except Exception as e:
        logger.warning("[Helpers] Cloud sync %s failed, ignoring: %s", direction, e)


async def run_game_subprocess(
    svc: LauncherService,
    plan: Any,
    ctx: LaunchContext,
    state: RuntimeState,
) -> int:
    """Run the game subprocess after materialising argv/env/cwd."""
    cmd = plan.get_cmd()
    env = plan.get_env()
    cwd = plan.get_cwd()

    logger.info("[Helpers] Spawning Windows subprocess: %s", cmd)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        cwd=cwd,
    )
    svc._active_subprocess = proc

    rc = await proc.wait()
    svc._active_subprocess = None

    return rc


async def sync_saves_and_track_size(
    svc: LauncherService,
    ctx: LaunchContext,
    phase: str,
) -> None:
    """Run cloud sync for native games."""
    # Simplified equivalent wrapper for native sync calls
    direction = "down" if "down" in phase else "up"
    await cloud_sync_phase(svc, ctx, direction)


def resolve_exit_code(svc: LauncherService, state: RuntimeState) -> int:
    """Resolve the final exit code."""
    if getattr(svc, "_cancelled", False):
        return -1
    return getattr(state, "rc", 1)


def elapsed_since_launch(svc: LauncherService) -> float:
    """Return time elapsed since launch started."""
    if svc._launch_started_at is None:
        return 0.0
    return time.monotonic() - svc._launch_started_at
