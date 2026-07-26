"""compat/rockstar_egs.py — Rockstar-on-Epic (RDR2 / GTA5) launch setup.

RDR2 and GTA5 bought on the Epic Games Store boot the **Rockstar Games
Launcher**, which needs the Epic Games Launcher present to link/verify the
Epic entitlement before it will treat the game as installed and playable.
To make that chain work under Proton/umu we automate what testers
otherwise do by hand:

  1. Drop a **fake** ``EpicGamesLauncher.exe`` (a tiny stub, NOT the real
     launcher — Heroic's ``USE_FAKE_EPIC_EXE``, bundled at
     ``bin/EpicGamesLauncher.exe``) into the game's install dir.
  2. Write a small launch shim ``.bat`` beside it that starts the game
     **through** that stub: ``start "" EpicGamesLauncher.exe
     PlayGTAV.exe %*``. ``handlers.epic`` then points legendary's
     ``--override-exe`` at the shim.
  3. Register the ``com.epicgames.launcher`` protocol handler in the
     prefix, so the protocol-based half of the handoff also resolves.

Step 2 is load-bearing and was the missing piece in the first cut of this
flow. That version assumed the Rockstar launcher performed the
``EpicGamesLauncher.exe PlayRDR2.exe`` handoff *itself*, so it pointed
``--override-exe`` straight at ``PlayGTAV.exe``. Launching the Play exe
directly is exactly the broken case a tester reported: the Rockstar
launcher finds the game on the very first boot but refuses to start it,
and after a restart no longer finds it at all. Going through the stub is
what makes it offer the Epic-account link and then work. (Reproduced and
fixed manually by the reporter with a hand-written ``fix.bat`` + "change
executable"; this module does both automatically.)

Every step is best-effort and — crucially — only ever runs for the
Rockstar titles (:func:`game_fixes.is_rockstar_egs`); ordinary Epic games
never reach here, so the standard Epic flow is byte-for-byte unchanged.
The complementary halves live in ``core.proton_prepare`` (STORE=egs +
WINEDLLOVERRIDES) and ``epic_cleanup`` (skips the stub/registry removal
for these games).

Online play never works on Linux (BattlEye has no Linux support) — this
enables story mode only.
"""
from __future__ import annotations

import logging
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.prefix_layout import (
    resolve_drive_c,
    resolve_registry_prefix,
)

logger = logging.getLogger(__name__)

_FAKE_LAUNCHER_NAME = "EpicGamesLauncher.exe"
# Name of the generated launch shim, written into the game's install dir and
# used as legendary's ``--override-exe``. Deliberately namespaced: testers
# following the manual workaround hand-write their own ``fix.bat`` in the same
# directory, and overwriting that would clobber their work.
LAUNCH_SHIM_NAME = "unifideck-rockstar-launch.bat"
# The shim body, verbatim from the reporter's proven ``fix.bat`` — do not
# "clean this up". ``start ""`` supplies the empty window-title argument cmd
# requires before the command (without it cmd treats the exe name AS the
# title and silently does nothing), and ``%*`` forwards any game args
# legendary appends. Single LF endings, matching the file that was verified
# working on-device.
_LAUNCH_SHIM_TEMPLATE = 'start "" {launcher} {play_exe} %*\n'
# Wine .reg block that registers the com.epicgames.launcher URL protocol
# so the Rockstar launcher's Epic handoff resolves. Mirrors Heroic's
# ``reg add HKEY_CLASSES_ROOT\com.epicgames.launcher /f``.
_EPIC_PROTOCOL_REG = (
    "\n[Software\\\\Classes\\\\com.epicgames.launcher]\n"
    '@="URL:com.epicgames.launcher"\n'
    '"URL Protocol"=""\n'
)
_EPIC_PROTOCOL_MARKER = "[Software\\\\Classes\\\\com.epicgames.launcher]"


def apply_rockstar_egs_setup(plan: ProtonLaunchPlan) -> None:
    """Best-effort Rockstar-on-Epic prefix + install-dir setup.

    No-op unless ``plan`` is a Rockstar-EGS game. Never raises — a
    failure here should degrade to the ordinary (likely-failing) launch,
    not abort it.
    """
    from unifideck.launcher.proton.fixes.game_fixes import is_rockstar_egs
    if not is_rockstar_egs(
        plan.context.game_id, plan.state.umu_id, plan.context.exe_path.name,
        _resolve_install_dir(plan),
    ):
        return
    logger.info(
        "[rockstar_egs] applying setup for %s (%s)",
        plan.context.game_key, plan.state.umu_id,
    )
    try:
        _copy_fake_launcher(plan)
    except Exception:
        logger.exception("[rockstar_egs] fake-launcher copy failed")
    try:
        _write_launch_shim(plan)
    except Exception:
        logger.exception("[rockstar_egs] launch-shim write failed")
    try:
        _register_epic_protocol(plan)
    except Exception:
        logger.exception("[rockstar_egs] protocol registration failed")


def _resolve_install_dir(plan: ProtonLaunchPlan) -> Path | None:
    """The game's install dir (where the stub + shim belong), or ``None``.

    The Rockstar bootstrap runs ``EpicGamesLauncher.exe`` from the game's own
    directory, so both artefacts must sit next to the game exe — not in the
    prefix.
    """
    work_dir = plan.context.work_dir or plan.context.exe_path.parent
    if not work_dir or not Path(work_dir).is_dir():
        return None
    return Path(work_dir)


def _write_launch_shim(plan: ProtonLaunchPlan) -> None:
    """Write the ``start "" EpicGamesLauncher.exe <PlayExe> %*`` shim.

    This is the piece that makes the Rockstar launcher verify the Epic
    entitlement instead of refusing to start / "losing" the install — see the
    module docstring. Rewrites whenever the content differs so a corrected
    template propagates to already-set-up installs, rather than being skipped
    forever because *a* file happened to exist.
    """
    from unifideck.launcher.proton.fixes.game_fixes import (
        resolve_rockstar_play_exe,
    )
    install_dir = _resolve_install_dir(plan)
    if install_dir is None:
        logger.warning("[rockstar_egs] install dir missing — skipping shim")
        return
    play_exe = resolve_rockstar_play_exe(
        plan.context.game_id, plan.state.umu_id, plan.context.exe_path.name,
        install_dir,
    )
    if not play_exe:
        logger.warning(
            "[rockstar_egs] no Play exe resolved for %s — skipping shim",
            plan.context.game_id,
        )
        return
    content = _LAUNCH_SHIM_TEMPLATE.format(
        launcher=_FAKE_LAUNCHER_NAME, play_exe=play_exe,
    )
    dest = install_dir / LAUNCH_SHIM_NAME
    if dest.is_file() and dest.read_text(encoding="utf-8") == content:
        logger.info("[rockstar_egs] launch shim already current: %s", dest)
        return
    dest.write_text(content, encoding="utf-8")
    logger.info("[rockstar_egs] wrote launch shim %s → %s", dest, play_exe)


def _copy_fake_launcher(plan: ProtonLaunchPlan) -> None:
    """Copy the bundled fake ``EpicGamesLauncher.exe`` beside the game exe.

    The generated launch shim runs ``EpicGamesLauncher.exe <PlayExe>`` from
    the game's own directory, so the stub must sit next to the game exe (the
    install/work dir), not in the prefix.
    """
    import shutil
    fake = plan.context.plugin_dir / "bin" / _FAKE_LAUNCHER_NAME
    if not fake.is_file():
        logger.warning(
            "[rockstar_egs] bundled %s missing at %s — skipping",
            _FAKE_LAUNCHER_NAME, fake,
        )
        return
    install_dir = _resolve_install_dir(plan)
    if install_dir is None:
        logger.warning(
            "[rockstar_egs] install dir missing — skipping fake launcher",
        )
        return
    dest = install_dir / _FAKE_LAUNCHER_NAME
    if dest.is_file():
        logger.info("[rockstar_egs] fake launcher already present: %s", dest)
        return
    shutil.copy2(fake, dest)
    logger.info("[rockstar_egs] installed fake launcher: %s", dest)


def _register_epic_protocol(plan: ProtonLaunchPlan) -> None:
    """Append the com.epicgames.launcher protocol block to user.reg.

    Idempotent — skips if the block is already present. Writing the .reg
    directly (rather than a umu-run regedit) keeps this a cheap, offline
    file edit; Wine reads user.reg at prefix load.
    """
    registry_root = resolve_registry_prefix(plan.prefix_path)
    user_reg = registry_root / "user.reg"
    # drive_c must exist for the prefix to be real; if not, the prefix
    # hasn't been created yet and there's nothing to register into.
    if resolve_drive_c(plan.prefix_path) is None or not user_reg.is_file():
        logger.info(
            "[rockstar_egs] user.reg not ready (%s) — skipping protocol reg",
            user_reg,
        )
        return
    content = user_reg.read_text(encoding="utf-8", errors="replace")
    if _EPIC_PROTOCOL_MARKER in content:
        logger.info("[rockstar_egs] epic protocol already registered")
        return
    user_reg.write_text(content + _EPIC_PROTOCOL_REG, encoding="utf-8")
    logger.info("[rockstar_egs] registered com.epicgames.launcher protocol")
