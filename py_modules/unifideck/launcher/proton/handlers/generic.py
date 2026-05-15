from __future__ import annotations

import logging
import shutil
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

logger = logging.getLogger(__name__)
def _locate_store_cli(plan: ProtonLaunchPlan, tool_name: str) -> Path | None:
    """Locate store cli."""
    plugin_bin = plan.context.plugin_dir / "bin" / tool_name
    if plugin_bin.is_file():
        return plugin_bin
    system = shutil.which(tool_name)
    return Path(system) if system else None
async def _gog_launch(plan: ProtonLaunchPlan) -> int:
    """Gog launch."""
    try:
        from unifideck.config.config_manager import ConfigManager
        from unifideck.launcher.proton.language_setup import apply_gog_language
        _cfg = ConfigManager(
            str(plan.context.plugin_dir / "defaults" / "config.json"),
        )
        work_dir = plan.context.work_dir or plan.context.exe_path.parent
        apply_gog_language(
            plan.context.game_id, str(work_dir), config=_cfg,
        )
    except Exception as err:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
        logger.warning(
            "[launcher.proton.generic] GOG language setup failed: %s",
            err,
        )
    try:
        from unifideck.launcher.proton.fixes.galaxy_stub import install_galaxy_stub
        install_galaxy_stub(
            str(plan.prefix_path),
            plugin_dir=plan.context.plugin_dir,
        )
    except Exception as err:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
        logger.warning(
            "[launcher.proton.generic] Galaxy stub install failed: %s",
            err,
        )
    gogdl = _locate_store_cli(plan, "gogdl")
    if gogdl:
        logger.info("[launcher.proton.generic] GOG via gogdl: %s", gogdl)
        argv: list[str] = list(plan.state.wrappers)
        argv.extend([
            str(gogdl),
            "launch",
            plan.context.game_id,
            "--wrapper",
            f"{plan.python_bin} {plan.umu_wrapper}",
        ])
        if plan.state.game_args:
            argv.append("--")
            argv.extend(plan.state.game_args)
        return await run_umu_with_retry(argv, env=plan.env, on_start=plan.on_process_start)
    return await _raw_exe_launch(plan)

async def _amazon_launch(plan: ProtonLaunchPlan) -> int:

    """Amazon launch."""
    try:
        from unifideck.config.config_manager import ConfigManager
        from unifideck.launcher.proton.language_setup import apply_amazon_language
        _cfg = ConfigManager(
            str(plan.context.plugin_dir / "defaults" / "config.json"),
        )
        apply_amazon_language(str(plan.prefix_path), config=_cfg)
    except Exception as err:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
        logger.warning(
            "[launcher.proton.generic] Amazon language setup failed: %s",
            err,
        )
    nile = _locate_store_cli(plan, "nile")
    if nile:
        logger.info("[launcher.proton.generic] Amazon via nile: %s", nile)
        argv: list[str] = list(plan.state.wrappers)
        argv.extend([
            str(nile),
            "launch",
            plan.context.game_id,
            "--wrapper",
            f"{plan.python_bin} {plan.umu_wrapper}",
        ])
        if plan.state.game_args:
            argv.append("--")
            argv.extend(plan.state.game_args)
        return await run_umu_with_retry(argv, env=plan.env, on_start=plan.on_process_start)
    return await _raw_exe_launch(plan)
async def _raw_exe_launch(plan: ProtonLaunchPlan) -> int:
    """Raw exe launch."""
    logger.info(
        "[launcher.proton.generic] raw exe launch: %s", plan.context.exe_path,
    )
    cwd: Path | None = None
    if plan.context.exe_path.parent.is_dir():
        cwd = plan.context.exe_path.parent
    argv: list[str] = list(plan.state.wrappers)
    argv.extend([
        str(plan.python_bin),
        str(plan.umu_wrapper),
        str(plan.context.exe_path),
    ])
    argv.extend(plan.state.game_args)
    return await run_umu_with_retry(argv, env=plan.env, cwd=cwd, on_start=plan.on_process_start)
async def generic_launch(plan: ProtonLaunchPlan) -> int:
    """Generic launch."""
    store = plan.context.store
    if store == "gog":
        rc = await _gog_launch(plan)
    elif store == "amazon":
        rc = await _amazon_launch(plan)
    else:
        rc = await _raw_exe_launch(plan)
    plan.state.game_exit_code = rc
    if rc == 0:
        return 0
    if rc in {2, 74}:
        raise UmuRuntimeError(
            f"umu-run failed with unrecoverable code {rc}",
            context={"subprocess_rc": rc, "store": store},
        )
    raise GameFailedError(
        f"{store} game exited with code {rc}",
        subprocess_rc=rc,
        context={"store": store, "game_id": plan.context.game_id},
    )
