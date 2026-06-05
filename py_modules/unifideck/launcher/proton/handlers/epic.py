from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from unifideck.launcher.proton.compat.epic_cleanup import cleanup_epic_artifacts
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

logger = logging.getLogger(__name__)


def _resolve_exe_override(plan: ProtonLaunchPlan) -> Path | None:
    """Resolve exe override."""
    from unifideck.launcher.proton.fixes.game_fixes import get_exe_override
    rel = get_exe_override(plan.context.game_id)
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
    logger.info(
    "[launcher.proton.epic] launching %s", plan.context.game_key,
   )
    from unifideck.launcher.proton.compat.epic import (
        apply_eos_overlay,
        build_legendary_env,
        detect_offline,
        resolve_legendary_bin,
        resolve_legendary_config_path,
    )

    cleanup_epic_artifacts(plan)
    await _run_epic_prerequisites(plan)

    config_path = resolve_legendary_config_path()
    legendary_bin = resolve_legendary_bin(plan.context.plugin_dir)

    # EOS / EGS overlay: install (once) + enable for this prefix. Some
    # titles (e.g. Football Manager) need it. Best-effort — never blocks.
    try:
        await apply_eos_overlay(plan, legendary_bin, config_path)
    except Exception:
        logger.exception(
            "[launcher.proton.epic] EOS overlay step failed (non-fatal)",
        )

    env = build_legendary_env(plan, config_path)
    exe_override = _resolve_exe_override(plan)
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
        f"{plan.python_bin} {plan.umu_wrapper}",
        "--language",
        os.environ.get("EPIC_LANG", "en"),
    ])
    if exe_override:
        argv.extend(["--override-exe", str(exe_override)])
        logger.info(
            "[launcher.proton.epic] using EXE override: %s",
            exe_override,
        )
    if plan.state.game_args:
        argv.append("--")
        argv.extend(plan.state.game_args)
    rc = await run_umu_with_retry(
        argv, env=env, on_start=plan.on_process_start,
    )
    # NOTE: legendary returns the instant it spawns umu (Popen, no
    # wait), so ``rc`` reflects legendary, not the game — the game runs
    # in an orphaned umu/Proton tree and survives this process exiting
    # (this is how Epic launched fine before). We deliberately do NOT
    # block on a wait-for-container loop here: a broad process match
    # snags Steam's own ``steam-runtime-launch-client`` in Gaming Mode
    # and hangs the launcher forever, which is what broke launches.
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
        context={
            "store": "epic",
            "game_id": plan.context.game_id,
        },
    )
