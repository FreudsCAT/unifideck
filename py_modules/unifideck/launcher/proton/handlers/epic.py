from __future__ import annotations

import json
import logging
from pathlib import Path

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.compat.epic_cleanup import cleanup_epic_artifacts
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

logger = logging.getLogger(__name__)


def _rockstar_play_exe_rel(plan: ProtonLaunchPlan) -> str | None:
    """The ``--override-exe`` target (relative) for RDR2/GTA5, else None.

    Prefers the launch shim ``compat.rockstar_egs`` just wrote, which starts
    the game **through** the fake ``EpicGamesLauncher.exe``. That indirection
    is the whole point: launching ``PlayGTAV.exe``/``PlayRDR2.exe`` directly
    is the reported failure (Rockstar launcher finds the game once, refuses
    to start it, then stops finding it) because the Epic entitlement is never
    verified.

    Falls back to the bare Play exe if the shim isn't on disk — same
    behaviour as before, so a shim-write failure degrades instead of
    breaking the launch outright. A user's explicit "Change executable"
    still wins (checked first in ``_resolve_exe_override``), which is what
    lets a hand-written ``fix.bat`` keep working.
    """
    from unifideck.launcher.proton.compat.rockstar_egs import LAUNCH_SHIM_NAME
    from unifideck.launcher.proton.fixes.game_fixes import (
        resolve_rockstar_play_exe,
    )
    work_dir = plan.context.work_dir
    play_exe = resolve_rockstar_play_exe(
        plan.context.game_id, plan.state.umu_id, plan.context.exe_path.name,
        work_dir,
    )
    if not play_exe:
        return None
    if work_dir and (Path(work_dir) / LAUNCH_SHIM_NAME).is_file():
        return LAUNCH_SHIM_NAME
    logger.warning(
        "[launcher.proton.epic] Rockstar launch shim absent — falling back to "
        "direct %s launch (Rockstar launcher may not detect the install)",
        play_exe,
    )
    return play_exe


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


def _resolve_epic_language(plan: ProtonLaunchPlan) -> str:
    """Resolve the Epic language code from the Unifideck config.

    Reads the user's language preference via ``get_unifideck_locale``
    (which checks ``ui.locale`` → system POSIX → fallback) and converts
    the BCP-47 tag to a 2-letter Epic language code (e.g. ``es-ES`` →
    ``es``).  Falls back to ``en`` if anything goes wrong.
    """
    try:
        # ``resolve_user_config_path`` lives in the submodule, NOT in the
        # ``unifideck.config`` package namespace (its ``__all__`` only
        # re-exports ConfigManager / persistence / validator). Importing it
        # from the package raised ImportError on every single launch, the
        # ``except`` below swallowed it, and every Epic game got
        # ``--language en`` no matter what the UI said.
        from unifideck.config import ConfigManager
        from unifideck.config.user_config_path import resolve_user_config_path
        from unifideck.utils.locale import get_unifideck_locale

        config = ConfigManager(
            defaults_path=str(
                plan.context.plugin_dir / "defaults" / "config.json",
            ),
            user_path=resolve_user_config_path(),
        )
        locale_tag = get_unifideck_locale(config)
        # BCP-47 → 2-letter prefix for legendary --language
        lang = locale_tag.split("-")[0].lower()
        if lang and len(lang) == 2:
            logger.info(
                "[launcher.proton.epic] resolved language %s from "
                "config locale %s", lang, locale_tag,
            )
            return lang
    except Exception:
        logger.exception("[launcher.proton.epic] language resolution "
                         "failed, falling back to 'en'")
    return "en"


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
        _resolve_epic_language(plan),
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
