"""services/launcher/helpers.py — Technical primitives for launch flows.

6 functions supporting the public orchestrators
(``launch_windows`` / ``launch_native``). All take a
``LauncherService`` as first arg (``svc``). Byte-identical
behaviour to the pre-extraction versions — split out for volumetry.
"""
from __future__ import annotations

import functools
import logging
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext, RuntimeState

    from .service import LauncherService

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _cffi_backend_available() -> bool:
    """Whether the host's system Python can import cffi's native backend.

    The out-of-process launcher runs under the host ``/usr/bin/python3``,
    whose minor version varies across distros (SteamOS/Bazzite/CachyOS).
    The cloud-save service imports cryptography → cffi → ``_cffi_backend``,
    an ABI-specific ``.so`` we vendor per Python version at build time. When
    the host Python has no matching backend, the cloud-save service fails to
    instantiate and ``svc._cloud_svc`` is ``None`` — this probe lets us tell
    that specific cause apart so the user gets an actionable toast instead of
    silence. Cached: the answer can't change within a process.
    """
    try:
        import _cffi_backend  # noqa: F401  # ABI probe only
    except Exception:
        return False
    return True


async def _emit_cloud_unavailable_toast(
    svc: LauncherService, ctx: LaunchContext,
) -> None:
    """Warn once that cloud-save is off because of an unsupported host Python.

    Best-effort and fully isolated — any failure here is swallowed so it can
    never affect the launch.
    """
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    logger.warning(
        "[Helpers] cloud-save disabled: system Python %s has no matching "
        "cffi backend (vendor it in build-plugin.sh LAUNCHER_PYTHON_VERSIONS)",
        py_ver,
    )
    try:
        from unifideck.launcher.rpc import emit_stage

        await emit_stage(
            svc._bus,
            i18n_key="cloudSync.unavailableNativeDep",
            game_title=getattr(ctx, "game_key", None)
            or f"{ctx.store}:{ctx.game_id}",
            priority="low",
            severity="warning",
            i18n_params={"python": py_ver},
        )
    except Exception as e:  # never let a toast break a launch
        logger.debug("[Helpers] cloud-unavailable toast failed: %s", e)


async def prepare_windows_plan(
    svc: LauncherService,
    ctx: LaunchContext,
    state: RuntimeState,
) -> tuple[Any, Any]:
    """Prepare the Proton launch plan for a Windows game.

    Resolves the three things ``proton_prepare`` needs — a
    Python 3.10+ interpreter, the Proton tool path, and its
    tool id — then builds the immutable ``ProtonLaunchPlan``
    the store handlers consume. Proton is selected by
    ``select_proton_version``, which honours (in order) the
    per-game tool the frontend captured into
    ``proton_settings.json``, any Steam compat override, the
    Unifideck default, and finally a GE-Proton fallback.

    The ``on_process_start`` callback registers the spawned
    process on the service so SIGTERM/SIGINT cancellation can
    reach it (mirrors the native path's ``_active_subprocess``).
    """
    from unifideck.launcher.proton import (
        find_python_3_10_plus,
        proton_prepare,
        select_proton_version,
    )

    try:
        python_bin = find_python_3_10_plus()
        proton_path, proton_tool_id = select_proton_version(
            steam_app_id=ctx.steam_app_id,
            store_game_id=ctx.game_key,
        )
        def _on_process_start(proc: object) -> None:
            svc._active_subprocess = proc

        # ``proton_prepare`` is synchronous (prefix mkdir + umu-id
        # lookup); call it directly — the launcher subprocess has
        # nothing else on its event loop.
        plan = proton_prepare(
            ctx,
            state,
            python_bin=python_bin,
            proton_path=proton_path,
            proton_tool_id=proton_tool_id,
            on_process_start=_on_process_start,
        )
        # parsed_options reserved for LSFG/wrapper parsing.
        return plan, None
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

    # Cloud-save is optional: the launcher may have been built without it
    # (e.g. the service failed to instantiate). A launch must never depend
    # on cloud-save being present, so skip when it's unavailable.
    if svc._cloud_svc is None:
        logger.debug(
            "[Helpers] Cloud sync %s skipped — cloud service unavailable",
            direction,
        )
        # If the cause is a host Python with no matching cffi backend
        # (the common non-SteamOS failure mode), tell the user once —
        # on the "down" phase, which always runs first — instead of
        # leaving them to wonder why saves never sync. Best-effort:
        # the toast must never block or fail a launch.
        if direction == "down" and not _cffi_backend_available():
            await _emit_cloud_unavailable_toast(svc, ctx)
        return

    # Respect the auto-sync config flags. Download-on-launch is on by default;
    # upload-on-stop is OFF by default (manual via the cloud-save button), so
    # this is the path that must honour ``cloud.auto_push_on_stop``.
    if hasattr(svc._cloud_svc, "auto_sync_enabled") and not svc._cloud_svc.auto_sync_enabled(direction):
        logger.info(
            "[Helpers] Cloud sync %s skipped — disabled by config", direction,
        )
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
    """Run the Windows game via the per-store Proton handler.

    Delegates to ``proton.dispatch`` which routes the
    ``ProtonLaunchPlan`` to the right store handler
    (epic / ubisoft / generic) and runs it through umu-run.
    The spawned process is registered on the service via the
    plan's ``on_process_start`` callback (wired in
    ``prepare_windows_plan``), so cancellation can reach it;
    we clear the reference once the handler returns.
    """
    from unifideck.launcher.proton import dispatch

    logger.info(
        "[Helpers] Dispatching Proton launch: store=%s game_id=%s proton=%s",
        ctx.store, ctx.game_id, state.proton_tool_id,
    )
    try:
        rc = await dispatch(plan)
    finally:
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
