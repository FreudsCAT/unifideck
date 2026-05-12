"""Launch sub-routines — Windows and native launch paths.

OP-20d | py_modules/unifideck/services/launcher/orchestrator.py

Two functions, one per launch type :

* ``launch_windows`` — Proton-based path : compose the
  ``proton run`` command, set up the Wine prefix env vars, etc.
* ``launch_native``  — direct binary invocation for Linux-native
  games (rare on the Decky-targeted store set).

Both return a ``LaunchPlan`` consumed by the service's main loop.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from ...core.types import Result
from ...launcher.rpc import emit_game_launched, emit_game_stopped
from ...launcher.types.errors import LauncherError
from .helpers import (
    cloud_sync_phase,
    elapsed_since_launch,
    prepare_windows_plan,
    resolve_exit_code,
    run_game_subprocess,
    sync_saves_and_track_size,
)

if TYPE_CHECKING:
    from ...launcher.types.context import LaunchContext, RuntimeState
    from .service import LauncherService
logger = logging.getLogger(__name__)


async def launch_windows(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> Result:
    """Drive the full Windows-game launch pipeline.

    Pipeline:

    1. **Plan build** — compose the Proton command (env, prefix,
       compat tool, argv) into a ``ProtonLaunchPlan``.
    2. **Cloud sync down** — pull remote saves before launch so
       the user picks up where they left off.
    3. **Emit GAME_LAUNCHED** — fires playtime tracking + other
       launch-aware services.
    4. **Run subprocess** — spawn Proton, await its exit, capture
       the return code.
    5. **Cloud sync up** — push fresh saves after exit so they
       survive across devices.
    6. **Wrap as Result** — non-zero exit → ``Result.error_code =
       "exit_<N>"`` (consumed by launch-history for circuit
       breaker classification).

    ``LauncherError`` is re-raised intact so the service's
    top-level handler can transform it into a typed toast.

    Args:
        svc: the launcher service.
        ctx: launch context.
        state: mutable runtime state.

    Returns:
        ``Result`` with success/failure based on the subprocess
        exit code.
    """
    plan, _parsed = await prepare_windows_plan(svc, ctx, state)
    await cloud_sync_phase(svc, ctx, direction="down")
    await emit_game_launched(
        svc._bus,
        store=ctx.store,
        game_id=ctx.game_id,
    )
    try:
        rc = await run_game_subprocess(svc, plan, ctx, state)
        await cloud_sync_phase(svc, ctx, direction="up")
        return Result(
            success=(rc == 0),
            error=None if rc == 0 else f"game exited with code {rc}",
            error_code=None if rc == 0 else f"exit_{rc}",
            store=ctx.store,
        )
    except LauncherError:
        raise


async def launch_native(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> Result:
    """Drive the native-Linux launch pipeline.

    Mirrors ``launch_windows`` but skips the Proton plan and
    delegates the actual ``exec`` to
    ``launcher.flows.native.native_launch``. The launch options
    parser extracts wrappers (``gamemoderun``, ``mangohud``…),
    game args, and LSFG (latency-sensitive feature gate) flag
    from the raw options string.

    The GAME_STOPPED emission lives in a ``finally`` so playtime
    tracking gets a stop event even when ``native_launch``
    throws.

    Args:
        svc: the launcher service.
        ctx: launch context.
        state: mutable runtime state.

    Returns:
        ``Result`` from ``native_launch``.
    """
    from ...launcher.flows.native import native_launch
    from ...launcher.types.options import parse_launch_options

    parsed = parse_launch_options(ctx.raw_options)
    state.wrappers = list(parsed.wrappers)
    state.game_args = list(parsed.game_args)
    state.lsfg_requested = parsed.lsfg_requested
    await sync_saves_and_track_size(svc, ctx, phase="sync_down")
    await emit_game_launched(
        svc._bus,
        store=ctx.store,
        game_id=ctx.game_id,
    )
    try:
        result = await native_launch(ctx, state)
    finally:
        exit_code = resolve_exit_code(svc, state)
        elapsed = elapsed_since_launch(svc)
        await emit_game_stopped(
            svc._bus,
            store=ctx.store,
            game_id=ctx.game_id,
            exit_code=exit_code,
            elapsed_seconds=elapsed,
            terminated_by_signal=(svc._signal_state.terminated_by_signal),
        )
    await sync_saves_and_track_size(svc, ctx, phase="sync_up")
    return result
