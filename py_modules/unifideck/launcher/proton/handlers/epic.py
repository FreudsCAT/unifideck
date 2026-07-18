from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.compat.epic_cleanup import cleanup_epic_artifacts
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

logger = logging.getLogger(__name__)


def _rockstar_play_exe_rel(plan: ProtonLaunchPlan) -> str | None:
    """The Rockstar Play-launcher exe (relative) for RDR2/GTA5, else None.

    Used as the default ``--override-exe`` for these titles so legendary
    launches ``PlayRDR2.exe``/``PlayGTAV.exe`` directly instead of the
    Epic-launcher stub. A user's explicit "Change executable" still wins
    (checked first in ``_resolve_exe_override``).
    """
    from unifideck.launcher.proton.fixes.game_fixes import (
        ROCKSTAR_PLAY_EXES,
        is_rockstar_egs,
    )
    game_id = plan.context.game_id
    umu_id = plan.state.umu_id
    if not is_rockstar_egs(game_id, umu_id):
        return None
    # ROCKSTAR_PLAY_EXES is keyed by BOTH the Epic app name and the umu id.
    return ROCKSTAR_PLAY_EXES.get(game_id) or ROCKSTAR_PLAY_EXES.get(umu_id or "")


def _resolve_exe_override(plan: ProtonLaunchPlan) -> Path | None:
    """Resolve exe override."""
    from unifideck.launcher.proton.fixes.game_fixes import get_exe_override
    # User "Change executable" / curated MANUAL_FIXES wins; otherwise the
    # Rockstar Play exe for RDR2/GTA5 (None for every other Epic game).
    rel = get_exe_override(plan.context.game_id) or _rockstar_play_exe_rel(plan)
    if not rel:
        return None
    installed = Path(
        "~/.config/legendary/installed.json",
    ).expanduser()
    if not installed.is_file():
        return None
    try:
        with installed.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    install_path = (
        data.get(plan.context.game_id, {}).get("install_path")
    )
    if not install_path:
        return None
    full = Path(install_path) / rel
    return full if full.is_file() else None

async def _run_epic_prerequisites(plan: ProtonLaunchPlan) -> None:
    """Run epic prerequisites."""
    from unifideck.launcher.proton.fixes.epic_prerequisites import (
        apply_epic_prerequisites,
    )
    try:
        await apply_epic_prerequisites(plan)
    except Exception:
        logger.exception(
            "[launcher.proton.epic] prerequisites step crashed "
            "(non-fatal)",
        )


async def epic_launch(plan: ProtonLaunchPlan) -> int:

    """Epic launch."""
    logger.info("[launcher.proton.epic] launching %s", plan.context.game_key)
    launcher_toast(
        "toasts.launcher.startingEpicGame",
        i18n_title_key="toasts.launcher.launchingGame",
        game_title=plan.context.game_key,
    )
    cleanup_epic_artifacts(plan)
    await _run_epic_prerequisites(plan)
    # Rockstar-on-Epic (RDR2/GTA5) only: fake EpicGamesLauncher.exe + the
    # com.epicgames.launcher protocol handler. No-op for every other Epic
    # title (gated on the umu id), so the standard flow is unchanged.
    from unifideck.launcher.proton.compat.rockstar_egs import (
        apply_rockstar_egs_setup,
    )
    apply_rockstar_egs_setup(plan)
    legendary_bin, env = await _prepare_epic_env(plan)
    argv = _build_legendary_argv(plan, legendary_bin)
    rc = await run_umu_with_retry(
        argv, env=env, on_start=plan.on_process_start,
    )
    return _finish_epic_launch(plan, rc)


async def _prepare_epic_env(
    plan: ProtonLaunchPlan,
) -> tuple[str, dict[str, str]]:
    """Resolve the legendary binary + env, applying the EOS overlay once.

    The EOS/EGS overlay (needed by some titles, e.g. Football Manager)
    is best-effort and never blocks the launch.
    """
    from unifideck.launcher.proton.compat.epic import (
        apply_eos_overlay,
        build_legendary_env,
        resolve_legendary_bin,
        resolve_legendary_config_path,
    )
    config_path = resolve_legendary_config_path()
    legendary_bin = resolve_legendary_bin(plan.context.plugin_dir)
    try:
        await apply_eos_overlay(plan, legendary_bin, config_path)
    except Exception:
        logger.exception(
            "[launcher.proton.epic] EOS overlay step failed (non-fatal)",
        )
    return legendary_bin, build_legendary_env(plan, config_path)


def _build_legendary_argv(
    plan: ProtonLaunchPlan, legendary_bin: str,
) -> list[str]:
    """Assemble the ``legendary launch`` argv (offline, language, overrides)."""
    from unifideck.launcher.proton.compat.epic import detect_offline
    argv: list[str] = list(plan.state.wrappers)
    argv.extend([
        legendary_bin,
        "launch",
        plan.context.game_id,
        "--no-wine",
        "--skip-version-check",
    ])
    if detect_offline():
        argv.append("--offline")
        logger.info("[launcher.proton.epic] offline mode — passing --offline")
    argv.extend([
        "--wrapper",
        # legendary is a PyInstaller onefile binary; it may hand its own
        # bundled LD_LIBRARY_PATH/LD_PRELOAD down to this wrapper child
        # instead of restoring the clean env it was launched with. That
        # pollution then rides umu-run straight into the pressure-vessel
        # container, breaking the container's own python3 (missing
        # libz.so.1). Force-clear both right at the boundary.
        f"env -u LD_LIBRARY_PATH -u LD_PRELOAD {plan.python_bin} {plan.umu_wrapper}",
        "--language",
        os.environ.get("EPIC_LANG", "en"),
    ])
    exe_override = _resolve_exe_override(plan)
    if exe_override:
        argv.extend(["--override-exe", str(exe_override)])
        logger.info(
            "[launcher.proton.epic] using EXE override: %s", exe_override,
        )
    if plan.state.game_args:
        argv.append("--")
        argv.extend(plan.state.game_args)
    return argv


def _finish_epic_launch(plan: ProtonLaunchPlan, rc: int) -> int:
    """Record the exit code; raise on unrecoverable failures.

    legendary returns the instant it spawns umu (Popen, no wait), so
    ``rc`` reflects legendary, not the game — the game runs in an
    orphaned umu/Proton tree and survives this process exiting. We
    deliberately do NOT block on a wait-for-container loop: a broad
    process match snags Steam's own ``steam-runtime-launch-client`` in
    Gaming Mode and hangs the launcher forever.
    """
    plan.state.game_exit_code = rc
    if rc == 0:
        return 0
    if rc in {2, 74}:
        raise UmuRuntimeError(
            f"umu-run failed with unrecoverable code {rc}",
            context={"subprocess_rc": rc, "store": "epic"},
        )
    raise GameFailedError(
        f"Epic game exited with code {rc}",
        subprocess_rc=rc,
        context={"store": "epic", "game_id": plan.context.game_id},
    )
