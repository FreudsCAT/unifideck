from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

logger = logging.getLogger(__name__)
_ID_MAP_FILE = Path(
    "~/.local/share/unifideck/ubisoft_id_map.json",
).expanduser()


def _uplay_id_from_id_map(space_id: str) -> str | None:
    """Resolve the ``uplay://`` launch id for ``space_id`` from the id_map.

    Steam can't hand env vars to the launcher (it only forwards launch
    options as argv, and the dispatcher promotes ``UNIFIDECK_*`` tokens
    only), so ``UPLAY_ID`` is almost never set. Fall back to the id_map
    the backend persists, preferring the leveldb-sourced
    ``ubisoftconnect_game_id`` — the value ``uplay://launch/{id}/0``
    actually expects — then ``launch_id`` / ``install_id``. Returns
    ``None`` (caller drops to the Legendary path) on any read error.
    """
    try:
        data = json.loads(_ID_MAP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(space_id) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    for key in ("ubisoftconnect_game_id", "launch_id", "install_id"):
        value = entry.get(key)
        if value:
            return str(value)
    return None
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
    launcher_toast(
        "toasts.launcher.startingUbisoftGame",
        i18n_title_key="toasts.launcher.launchingGame",
        game_title=plan.context.game_key,
    )
    await _apply_epic_wrapper_fix(plan)
    if not await _inject_registry_keys(plan):
        logger.warning(
            "[launcher.proton.ubisoft] registry injection "
            "failed or skipped",
        )
    _apply_language_setup(plan)
    upc_exe = _find_upc_exe(plan)
    uplay_id = os.environ.get("UPLAY_ID") or _uplay_id_from_id_map(
        plan.context.game_id,
    )
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

async def ubisoft_auth_launch(plan: ProtonLaunchPlan) -> int:
    """Open Ubisoft Connect (UPC) in the auth prefix so the user signs in.

    The auth shortcut is *not* a game launch: unlike :func:`ubisoft_launch`
    (which opens a specific title via ``uplay://launch/{id}/0``), this
    just runs UPC bare in the ``.upc-auth`` prefix and keeps the process
    alive until the user closes it. That's what stops the Steam shortcut
    from exiting immediately — the bug where "something happened but the
    shortcut closed". The plugin's session monitor captures the
    credentials UPC writes once sign-in completes.

    Run directly (not via :func:`dispatch`) on purpose: ``dispatch`` runs
    ``ensure_prefix_initialized`` which can *reset* the prefix on a Proton
    family change — that would wipe the UPC install the plugin already
    built into the auth prefix.
    """
    logger.info(
        "[launcher.proton.ubisoft] auth launch — opening UPC in %s",
        plan.prefix_path,
    )
    # Sign-in is NOT a game launch — use a sign-in-specific toast instead of
    # the generic "Launching Game", which confused users clicking sign-in.
    launcher_toast(
        "toasts.launcher.signingInUbisoftMessage",
        i18n_title_key="toasts.launcher.signingInUbisoft",
        game_title="Ubisoft Connect",
    )
    upc_exe = _find_upc_exe(plan)
    if upc_exe is None:
        raise GameFailedError(
            "upc.exe not found in the Ubisoft auth prefix — the auth "
            "prefix may not be fully set up yet",
            subprocess_rc=127,
            context={"store": "ubisoft", "prefix": str(plan.prefix_path)},
        )
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(upc_exe)]
    logger.info("[launcher.proton.ubisoft] auth argv: %s", argv)
    rc = await run_umu_with_retry(
        argv, env=plan.env, on_start=plan.on_process_start,
    )
    plan.state.game_exit_code = rc
    logger.info("[launcher.proton.ubisoft] UPC auth session exited rc=%d", rc)
    return rc


async def ubisoft_install_launch(plan: ProtonLaunchPlan) -> int:
    """Open Ubisoft Connect (UPC) to install a game, via RunGame.

    The install shortcut is *not* a game launch: like
    :func:`ubisoft_auth_launch` it opens UPC and keeps the process alive
    until the user closes it, but it points UPC at the title's
    ``uplay://install/{id}`` deeplink (when the id resolves) so the
    install page opens directly; otherwise it opens UPC bare and the user
    picks the game. Because Steam launches this through ``RunGame``, UPC
    runs inside its own gamescope/XWayland session — so the window
    actually renders in Gaming Mode, which the old backend-subprocess
    spawn could not do (no session → invisible window).

    Run directly (NOT via :func:`dispatch`) on purpose — ``dispatch``
    runs ``ensure_prefix_initialized`` which can *reset* the per-game
    prefix the plugin just bootstrapped UPC into. The plugin's download
    worker watches the prefix for the installed files and finalises the
    queue item; this handler does not report install success itself.
    """
    logger.info(
        "[launcher.proton.ubisoft] install launch — opening UPC in %s",
        plan.prefix_path,
    )
    launcher_toast(
        "toasts.launcher.installingUbisoftMessage",
        i18n_title_key="toasts.launcher.installingUbisoft",
        game_title="Ubisoft Connect",
    )
    upc_exe = _find_upc_exe(plan)
    if upc_exe is None:
        raise GameFailedError(
            "upc.exe not found in the Ubisoft prefix — the per-game "
            "prefix may not be fully set up yet",
            subprocess_rc=127,
            context={"store": "ubisoft", "prefix": str(plan.prefix_path)},
        )
    uplay_id = os.environ.get("UPLAY_ID") or _uplay_id_from_id_map(
        plan.context.game_id,
    )
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(upc_exe)]
    if uplay_id:
        argv.append(f"uplay://install/{uplay_id}")
        logger.info(
            "[launcher.proton.ubisoft] install deeplink: uplay://install/%s",
            uplay_id,
        )
    else:
        logger.info(
            "[launcher.proton.ubisoft] no uplay id for %s — "
            "opening UPC bare for install",
            plan.context.game_id,
        )
    rc = await run_umu_with_retry(
        argv, env=plan.env, on_start=plan.on_process_start,
    )
    plan.state.game_exit_code = rc
    logger.info(
        "[launcher.proton.ubisoft] UPC install session exited rc=%d", rc,
    )
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
