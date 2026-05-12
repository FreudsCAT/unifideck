"""Launcher helpers — the bulk of the launch pipeline mechanics.

OP-20e | py_modules/unifideck/services/launcher/helpers.py

Module-level helpers that the service composes into the launch
pipeline :

* ``prepare_windows_plan`` — build the full Proton launch plan
  (env vars, working dir, executable, args, Proton version);
* ``cloud_sync_phase``     — orchestrate the pre-launch save sync
  with timeout + fallback;
* ``run_game_subprocess``  — spawn the subprocess, wire stdout/
  stderr to the audit log, return the exit code.

This module concentrates the moving parts that would clutter
``service.py`` ; keeping them here makes the service file readable
as a top-level pipeline.
"""

from __future__ import annotations
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from ...launcher.rpc import emit_game_stopped
from ...launcher.types.context import RuntimeState
from ...launcher.types.errors import LauncherError

if TYPE_CHECKING:
    from ...launcher.proton.infrastructure.core import ProtonLaunchPlan
    from ...launcher.types.context import LaunchContext
    from .service import LauncherService
logger = logging.getLogger(__name__)


async def prepare_windows_plan(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> tuple[ProtonLaunchPlan, object]:
    """Prepare windows plan."""
    from ...launcher.diagnostics.telemetry import PhaseTimer
    from ...launcher.proton.infrastructure.core import proton_prepare
    from ...launcher.proton.infrastructure.selector import (
        find_python_3_10_plus,
        select_proton_version,
    )
    from ...launcher.proton.infrastructure.umu_runtime import ensure_umu_runtime_ready
    from ...launcher.types.options import apply_lsfg_env, parse_launch_options

    parsed = parse_launch_options(ctx.raw_options)
    state.wrappers = list(parsed.wrappers)
    state.game_args = list(parsed.game_args)
    state.lsfg_requested = parsed.lsfg_requested
    async with PhaseTimer(
        svc._bus,
        "resolve_runtime",
        extra={"store": ctx.store},
    ):
        python_bin = find_python_3_10_plus()
        steam_app_id = os.environ.get(
            "UNIFIDECK_SHORTCUT_APPID",
        )
        proton_path, tool_id = select_proton_version(
            steam_app_id=steam_app_id,
        )
    async with PhaseTimer(
        svc._bus,
        "umu_runtime_ready",
        extra={"store": ctx.store},
    ):
        ensure_umu_runtime_ready()
    lsfg_overlay = apply_lsfg_env(parsed)
    async with PhaseTimer(
        svc._bus,
        "proton_prepare",
        extra={"store": ctx.store},
    ):
        plan = proton_prepare(
            ctx,
            state,
            python_bin=python_bin,
            proton_path=proton_path,
            proton_tool_id=tool_id,
            on_process_start=cast(
                "Callable[[object], None]",
                svc._registry.track,
            ),
        )
        plan.env.update(lsfg_overlay)
        plan.env.update(parsed.env_overrides)
    return plan, parsed


async def cloud_sync_phase(
    svc: LauncherService,
    ctx: LaunchContext,
    direction: str,
) -> None:
    """Cloud sync phase."""
    from pathlib import (
        Path as _P,
    )
    from ...launcher.cloud.disk_space import assert_enough_space
    from ...launcher.cloud.save_size_cache import (
        measure_directory_size,
        record_observed_size,
    )
    from ...launcher.diagnostics.telemetry import PhaseTimer

    async with PhaseTimer(
        svc._bus,
        f"cloud_sync_{direction}",
        extra={"store": ctx.store},
    ):
        try:
            assert_enough_space(
                _P(ctx.plugin_dir).expanduser(),
                svc._config,
                store=ctx.store,
                game_id=ctx.game_id,
            )
            if direction == "down":
                await svc._cloud_svc.sync_down(
                    ctx.store,
                    ctx.game_id,
                )
            else:
                await svc._cloud_svc.sync_up(
                    ctx.store,
                    ctx.game_id,
                )
            _local_dir = svc._cloud_svc.get_local_save_dir(
                ctx.store,
                ctx.game_id,
            )
            _size = measure_directory_size(_local_dir)
            if _size > 0:
                record_observed_size(
                    svc._config,
                    ctx.store,
                    ctx.game_id,
                    _size,
                )
        except Exception as err:
            from ...launcher.cloud.cloud_failure import (
                handle_cloud_sync_failure,
            )

            await handle_cloud_sync_failure(
                svc._bus,
                svc._config,
                phase=f"sync_{direction}",
                store=ctx.store,
                game_id=ctx.game_id,
                error=err,
            )


async def run_game_subprocess(
    svc: LauncherService,
    plan: ProtonLaunchPlan,
    ctx: LaunchContext,
    state: RuntimeState,
) -> int:
    """Run game subprocess."""
    from ...launcher import proton as proton_pkg
    from ...launcher.diagnostics.telemetry import PhaseTimer

    async with PhaseTimer(
        svc._bus,
        "game_run",
        extra={"store": ctx.store},
    ):
        try:
            rc = await proton_pkg.dispatch(plan)
        except LauncherError:
            raise
        finally:
            exit_code = state.game_exit_code
            if exit_code is None:
                exit_code = 1
            if svc._signal_state.terminated_by_signal:
                exit_code = 143
                state.terminated_by_signal = True
            import time as _t

            _elapsed = (
                _t.monotonic() - svc._launch_started_at
                if hasattr(svc, "_launch_started_at")
                else 0.0
            )
            await emit_game_stopped(
                svc._bus,
                store=ctx.store,
                game_id=ctx.game_id,
                exit_code=exit_code,
                elapsed_seconds=_elapsed,
                terminated_by_signal=(svc._signal_state.terminated_by_signal),
            )
    return rc


async def sync_saves_and_track_size(
    svc: LauncherService,
    ctx: LaunchContext,
    phase: str,
) -> None:
    """Sync saves and track size."""
    from pathlib import (
        Path as _P,
    )
    from ...launcher.cloud.cloud_failure import handle_cloud_sync_failure
    from ...launcher.cloud.disk_space import assert_enough_space
    from ...launcher.cloud.save_size_cache import (
        measure_directory_size,
        record_observed_size,
    )

    try:
        assert_enough_space(
            _P(ctx.plugin_dir).expanduser(),
            svc._config,
            store=ctx.store,
            game_id=ctx.game_id,
        )
        if phase == "sync_down":
            await svc._cloud_svc.sync_down(ctx.store, ctx.game_id)
        else:
            await svc._cloud_svc.sync_up(ctx.store, ctx.game_id)
        local_dir = svc._cloud_svc.get_local_save_dir(
            ctx.store,
            ctx.game_id,
        )
        size = measure_directory_size(local_dir)
        if size > 0:
            record_observed_size(
                svc._config,
                ctx.store,
                ctx.game_id,
                size,
            )
    except Exception as err:
        await handle_cloud_sync_failure(
            svc._bus,
            svc._config,
            phase=phase,
            store=ctx.store,
            game_id=ctx.game_id,
            error=err,
        )


def resolve_exit_code(svc: LauncherService, state: RuntimeState) -> int:
    """Resolve exit code."""
    if state.game_exit_code is not None:
        return state.game_exit_code
    if svc._signal_state.terminated_by_signal:
        state.terminated_by_signal = True
        return 143
    return 1


def elapsed_since_launch(svc: LauncherService) -> float:
    """Elapsed since launch."""
    if not hasattr(svc, "_launch_started_at"):
        return 0.0
    import time as _t

    return _t.monotonic() - svc._launch_started_at
