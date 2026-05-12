"""Launcher pipeline helpers — the moving parts of a game launch.

OP-20e | py_modules/unifideck/services/launcher/helpers.py

Module-level helpers that the service composes into the launch
pipeline. Each helper is a discrete phase wrapped in a
``PhaseTimer`` so telemetry can attribute the time spent in each
phase per launch:

* ``prepare_windows_plan`` — resolve Proton, ensure the UMU
  runtime is ready, and build the ``ProtonLaunchPlan``;
* ``cloud_sync_phase``     — pre-flight disk-space check + cloud
  sync (down or up) + observed-size recording;
* ``run_game_subprocess``  — actually dispatch the plan and emit
  GAME_STOPPED in the finally;
* ``sync_saves_and_track_size`` — same sync logic as
  ``cloud_sync_phase`` but for native launches (no PhaseTimer);
* ``resolve_exit_code`` / ``elapsed_since_launch`` — small synch
  utilities shared by orchestrator and service.

Keeping these here lets ``service.py`` read as a clean top-level
pipeline.
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
    """Build the ``ProtonLaunchPlan`` for a Windows game.

    Three timed phases:

    1. **resolve_runtime** — find a Python 3.10+ interpreter
       (umu needs it) and pick the right Proton version for
       the shortcut's AppID.
    2. **umu_runtime_ready** — verify (and lazily install) the
       umu runtime bundle.
    3. **proton_prepare** — assemble the actual plan via
       ``proton_prepare`` and overlay LSFG + user env overrides.

    Parsed launch options (wrappers, game args, LSFG flag) are
    pushed into the runtime state so downstream phases see them.

    The ``on_process_start`` hook registers the spawned PID with
    the launcher's signal-handler registry so SIGTERM /
    SIGINT during plugin shutdown propagates to the game.

    Args:
        svc: launcher service (provides bus + registry).
        ctx: launch context (carries store, game id, options).
        state: mutable runtime state populated with parsed
            options.

    Returns:
        Tuple ``(plan, parsed_options)``.
    """
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
    """Run a pre- or post-launch cloud-save sync, with safety nets.

    Workflow:

    1. **assert_enough_space** — fail fast if the plugin dir
       doesn't have enough free space for the save sync (using
       prior observed sizes from the cache).
    2. **sync** — call ``CloudSaveService.sync_down`` or
       ``sync_up`` depending on direction.
    3. **observe + record** — measure the local-save directory
       size after the sync and update the size cache so future
       runs have a better disk-space estimate.

    Any failure is routed to ``handle_cloud_sync_failure``
    (which decides whether to abort the launch or proceed with
    a degraded-mode warning). The entire phase is wrapped in a
    ``PhaseTimer`` for telemetry.

    Args:
        svc: launcher service.
        ctx: launch context.
        direction: ``"down"`` (pre-launch pull) or ``"up"``
            (post-launch push).
    """
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
    """Dispatch the Proton plan and emit GAME_STOPPED in the finally.

    Wraps ``launcher.proton.dispatch(plan)`` which spawns the
    subprocess and awaits its exit code. The whole block is
    inside a ``PhaseTimer("game_run")`` so the per-game
    runtime is captured for telemetry.

    Exit code resolution:

    * **state.game_exit_code** — set by ``dispatch`` from the
      subprocess return code.
    * **fallback to 1** — if dispatch errored out before
      setting it.
    * **override to 143** — if a signal terminated the process
      (143 = SIGTERM exit, the most useful canonical value).

    ``LauncherError`` is re-raised intact ; other exceptions are
    NOT caught here (they propagate up to the orchestrator).

    Args:
        svc: launcher service.
        plan: the prepared Proton plan.
        ctx: launch context.
        state: mutable runtime state.

    Returns:
        The subprocess exit code from ``proton_pkg.dispatch``.
    """
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
    """Native-launch variant of ``cloud_sync_phase``.

    Same workflow (disk-space check → sync → observed-size
    recording) but without the ``PhaseTimer`` wrapper because
    native launches don't go through the Proton telemetry
    pipeline.

    Args:
        svc: launcher service.
        ctx: launch context.
        phase: ``"sync_down"`` (pre) or ``"sync_up"`` (post).
    """
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
    """Compute the final exit code with signal-handler awareness.

    Priority:

    1. Explicit ``state.game_exit_code`` from the subprocess.
    2. SIGTERM-style code (143) if a signal terminated us.
    3. Fallback 1 — something failed before exit-code capture.

    Args:
        svc: launcher service (for signal state).
        state: runtime state to mutate with
            ``terminated_by_signal`` if applicable.

    Returns:
        Resolved exit code.
    """
    if state.game_exit_code is not None:
        return state.game_exit_code
    if svc._signal_state.terminated_by_signal:
        state.terminated_by_signal = True
        return 143
    return 1


def elapsed_since_launch(svc: LauncherService) -> float:
    """Compute wall-clock seconds since ``launch`` started.

    Returns 0 if ``_launch_started_at`` was never set — which
    happens when the launch flow short-circuited before setting
    it (e.g. circuit breaker refused immediately).

    Uses ``time.monotonic`` so NTP/clock adjustments during the
    game session can't produce negative or inflated durations.

    Args:
        svc: launcher service.

    Returns:
        Elapsed seconds, or 0.0 if no launch in progress.
    """
    if not hasattr(svc, "_launch_started_at"):
        return 0.0
    import time as _t

    return _t.monotonic() - svc._launch_started_at
