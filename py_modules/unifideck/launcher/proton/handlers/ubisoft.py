from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

logger = logging.getLogger(__name__)
async def _apply_epic_wrapper_fix(plan: ProtonLaunchPlan) -> None:
    """Apply EPIC wrapper fix."""
    from unifideck.launcher.proton.fixes.epic_prefix_fix import apply_epic_launcher_fix
    bundled_wrapper = (
    plan.context.plugin_dir / "bin" / "EpicGamesLauncher.exe"
   )
    if not bundled_wrapper.is_file():
        logger.warning(
        "[launcher.proton.ubisoft] EpicGamesLauncher.exe "
        "wrapper missing at %s",
        bundled_wrapper,
       )
        return
    try:
        await apply_epic_launcher_fix(
            prefix_path=plan.prefix_path,
            bundled_wrapper=bundled_wrapper,
        )
        logger.info(
            "[launcher.proton.ubisoft] Epic launcher wrapper applied",
        )
    except Exception:
        logger.exception(
            "[launcher.proton.ubisoft] Epic launcher wrapper fix failed",
        )
async def _inject_registry_keys(plan: ProtonLaunchPlan) -> bool:
    """Inject registry keys."""
    from unifideck.launcher.proton.fixes.epic_registry import setup_registry
    legendary_config = await asyncio.to_thread(lambda: Path("~/.config/legendary").expanduser())
    try:
        result = await setup_registry(
            game_id=plan.context.game_id,
            prefix_path=plan.prefix_path,
            legendary_config=legendary_config,
        )
        return result.success
    except Exception:
        logger.exception(
            "[launcher.proton.ubisoft] registry injection crashed",
        )
        return False

def _find_upc_exe(plan: ProtonLaunchPlan) -> Path | None:

    """Find UPC exe."""
    active_prefix = os.environ.get("ACTIVE_WINEPREFIX")
    candidates: list[Path] = []
    if active_prefix:
        candidates.append(
            Path(active_prefix)
            / "drive_c"
            / "Program Files (x86)"
            / "Ubisoft"
            / "Ubisoft Game Launcher"
            / "upc.exe",
        )
    candidates.append(
        plan.prefix_path
        / "drive_c"
        / "Program Files (x86)"
        / "Ubisoft"
        / "Ubisoft Game Launcher"
        / "upc.exe",
    )
    for c in candidates:
        if c.is_file():
            return c
    return None
async def ubisoft_launch(plan: ProtonLaunchPlan) -> int:
    """Ubisoft launch."""
    logger.info(
        "[launcher.proton.ubisoft] launching %s",
        plan.context.game_key,
    )
    await _apply_epic_wrapper_fix(plan)
    if not await _inject_registry_keys(plan):
        logger.warning(
            "[launcher.proton.ubisoft] registry injection "
            "failed or skipped",
        )
    _apply_language_setup(plan)
    upc_exe = _find_upc_exe(plan)
    uplay_id = os.environ.get("UPLAY_ID")
    if upc_exe and uplay_id:
        logger.info(
            "[launcher.proton.ubisoft] direct launch: "
            "uplay://launch/%s/0",
            uplay_id,
        )
        argv = [
            str(plan.python_bin),
            str(plan.umu_wrapper),
            str(upc_exe),
            f"uplay://launch/{uplay_id}/0",
        ]
        env = plan.env
    else:
        logger.warning(
            "[launcher.proton.ubisoft] upc.exe or UPLAY_ID "
            "missing, falling back to Legendary path",
        )
        argv, env = _build_legendary_fallback_argv(plan)
    rc = await run_umu_with_retry(
        argv, env=env, on_start=plan.on_process_start,
    )
    plan.state.game_exit_code = rc
    if rc == 0:
        return 0
    _raise_for_umu_rc(rc, plan)
    return rc

def _apply_language_setup(plan: ProtonLaunchPlan) -> None:

    """Apply language setup."""
    try:
        from unifideck.config.config_manager import ConfigManager
        from unifideck.launcher.proton.language_setup import apply_ubisoft_language
        _cfg = ConfigManager(
            str(plan.context.plugin_dir / "defaults" / "config.json"),
        )
        apply_ubisoft_language(
            str(plan.prefix_path),
            space_id=plan.context.game_id,
            config=_cfg,
        )
    except Exception as err:
        logger.warning(
            "[launcher.proton.ubisoft] language setup failed: %s",
            err,
        )
def _build_legendary_fallback_argv(
    plan: ProtonLaunchPlan,
) -> tuple[list[str], dict[str, str]]:
    """Build LEGENDARY fallback argv."""
    env = dict(plan.env)
    env["LEGENDARY_WRAPPER_EXE"] = (
        "C:\\windows\\command\\EpicGamesLauncher.exe"
    )
    legendary_bin = os.environ.get("LEGENDARY_BIN", "legendary")
    argv = [*plan.state.wrappers, legendary_bin, "launch", plan.context.game_id, "--no-wine", "--skip-version-check", "--wrapper", f"{plan.python_bin} {plan.umu_wrapper}", "--language", os.environ.get("EPIC_LANG", "en")]
    if plan.state.game_args:
        argv.append("--")
        argv.extend(plan.state.game_args)
    try:
        from unifideck.launcher.proton.fixes.auth_args_stripper import (
            strip_epic_auth_args,
        )
        argv, _stripped = strip_epic_auth_args(argv)
    except Exception as err:
        logger.warning(
            "[launcher.proton.ubisoft] auth args strip failed: %s",
            err,
        )
    return argv, env
def _raise_for_umu_rc(rc: int, plan: ProtonLaunchPlan) -> None:
    """Raise for UMU rc."""
    if rc in {2, 74}:
        raise UmuRuntimeError(
            f"umu-run failed with unrecoverable code {rc}",
            context={"subprocess_rc": rc, "store": "ubisoft"},
        ) from None
    raise GameFailedError(
        f"Ubisoft game exited with code {rc}",
        subprocess_rc=rc,
        context={
            "store": "ubisoft",
            "game_id": plan.context.game_id,
        },
    ) from None
