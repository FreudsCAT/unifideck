"""compat/rockstar_egs.py — Rockstar-on-Epic (RDR2 / GTA5) launch setup.

RDR2 and GTA5 bought on the Epic Games Store boot the **Rockstar Games
Launcher**, which then runs the real game exe (``PlayRDR2.exe`` /
``PlayGTAV.exe``). To make that chain work under Proton/umu we mirror what
Heroic automates (see its "Rockstar Games from Epic Games" wiki):

  1. Register the ``com.epicgames.launcher`` protocol handler in the
     prefix — the Rockstar launcher hands off to it via
     ``start EpicGamesLauncher.exe PlayRDR2.exe``.
  2. Drop a **fake** ``EpicGamesLauncher.exe`` (a tiny stub, NOT the real
     launcher) beside the game exe so that handoff resolves without the
     real Epic launcher being installed (Heroic's ``USE_FAKE_EPIC_EXE``).

Both steps are best-effort and — crucially — only ever run for the
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
    if not is_rockstar_egs(plan.state.umu_id):
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
        _register_epic_protocol(plan)
    except Exception:
        logger.exception("[rockstar_egs] protocol registration failed")


def _copy_fake_launcher(plan: ProtonLaunchPlan) -> None:
    """Copy the bundled fake ``EpicGamesLauncher.exe`` beside the game exe.

    The game's Play-launcher runs ``start EpicGamesLauncher.exe
    PlayRDR2.exe`` from its own directory, so the stub must sit next to
    the game exe (the install/work dir), not in the prefix.
    """
    import shutil
    fake = plan.context.plugin_dir / "bin" / _FAKE_LAUNCHER_NAME
    if not fake.is_file():
        logger.warning(
            "[rockstar_egs] bundled %s missing at %s — skipping",
            _FAKE_LAUNCHER_NAME, fake,
        )
        return
    work_dir = plan.context.work_dir or plan.context.exe_path.parent
    if not work_dir or not Path(work_dir).is_dir():
        logger.warning(
            "[rockstar_egs] install dir %s missing — skipping fake launcher",
            work_dir,
        )
        return
    dest = Path(work_dir) / _FAKE_LAUNCHER_NAME
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
