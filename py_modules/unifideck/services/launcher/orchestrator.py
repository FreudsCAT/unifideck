"""services/launcher/orchestrator.py — Per-platform launch entry points.

2 public orchestrators:
- ``launch_windows`` — Proton-wrapped pipeline (prepare plan,
  sync down, run subprocess, sync up).
- ``launch_native`` — native Linux, simpler: cloud sync wraps
  a direct subprocess, no Proton/umu/prefix setup.
``LauncherService.launch`` dispatches between them based on
``ctx.is_windows_game``. Heavy lifting in ``helpers.py``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.core.types import Result

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext, RuntimeState

    from .service import LauncherService

logger = logging.getLogger(__name__)


async def launch_windows(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> Result:
    """Windows game launch — 4-phase pipeline.

    1. ``prepare_windows_plan`` — options + runtime + umu + proton_prepare
    2. ``cloud_sync_phase("down")``
    3. ``run_game_subprocess`` — the actual game
    4. ``cloud_sync_phase("up")``
    """
    try:
        # Phase 1: Prepare
        plan, _parsed_options = await svc._prepare_windows_plan(ctx, state)

        from unifideck.core.types.events import Events
        store = ctx.store
        game_id = ctx.game_id

        # Phase 2: Cloud Sync Down
        await svc._cloud_sync_phase(ctx, "down")

        # Pre-launch event
        await svc._bus.emit(
            Events.GAME_LAUNCHED,
            store=store,
            game_id=game_id,
            title="",  # No title on LaunchContext
            app_id=0  # No app_id on LaunchContext
        )

        # Phase 3: Run Subprocess
        try:
            rc = await svc._run_game_subprocess(plan, ctx, state)
            state.rc = rc
        finally:
            # Emit GAME_STOPPED here so playtime records accurate duration
            await svc._bus.emit(Events.GAME_STOPPED, store=store, game_id=game_id)

        # Phase 4: Cloud Sync Up
        await svc._cloud_sync_phase(ctx, "up")

        exit_code = svc._resolve_exit_code(state)
        # ``Result`` has no ``rc`` field — its public surface is
        # ``success``, ``error``, ``error_code``, ``store``,
        # ``metadata``. The dispatcher's ``_map_result_to_exitcode``
        # parses ``error_code`` and extracts the integer from any
        # ``exit_<N>`` prefix; encoding the exit code there is the
        # documented round-trip channel for subprocess return codes.
        # The earlier ``rc=exit_code`` form raised
        # ``TypeError: Result.__init__() got an unexpected keyword
        # argument 'rc'`` on every launch — Windows games could
        # never report their exit code back to the launcher.
        return Result(
            success=(exit_code == 0),
            error_code=None if exit_code == 0 else f"exit_{exit_code}",
        )

    except Exception:
        logger.exception("[Orchestrator] Windows launch failed")
        raise  # Let the outer _handle_launcher_error catch and toast it


async def launch_native(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> Result:
    """Native Linux game launch — simpler path."""
    try:
        from unifideck.core.types.events import Events
        store = ctx.store
        game_id = ctx.game_id

        # Phase 1: Cloud Sync Down
        await svc._sync_saves_and_track_size(ctx, "sync_down")

        # Pre-launch event
        await svc._bus.emit(
            Events.GAME_LAUNCHED,
            store=store,
            game_id=game_id,
            title="",  # No title on LaunchContext
            app_id=0  # No app_id on LaunchContext
        )

        # Phase 2: Run Subprocess
        try:
            # For native games, we just run the executable directly
            import asyncio

            cmd = [str(ctx.exe_path)]
            # No launch_args on LaunchContext; if/when added,
            # extend ``cmd`` here.
            cmd.extend([])

            logger.info("[Orchestrator] Spawning native launch: %s", cmd)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(ctx.work_dir),
            )
            svc._active_subprocess = proc

            rc = await proc.wait()
            state.rc = rc
            svc._active_subprocess = None

        finally:
            await svc._bus.emit(Events.GAME_STOPPED, store=store, game_id=game_id)

        # Phase 3: Cloud Sync Up
        await svc._sync_saves_and_track_size(ctx, "sync_up")

        exit_code = svc._resolve_exit_code(state)
        # See the launch_windows path for the rationale — Result has
        # no ``rc`` field, exit codes round-trip via ``error_code``
        # (``exit_<N>`` prefix). The earlier ``rc=`` form raised
        # ``TypeError`` on every native-Linux launch.
        return Result(
            success=(exit_code == 0),
            error_code=None if exit_code == 0 else f"exit_{exit_code}",
        )

    except Exception:
        logger.exception("[Orchestrator] Native launch failed")
        raise
