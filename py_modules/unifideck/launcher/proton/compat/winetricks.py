"""compat/winetricks.py — first-launch Windows redistributables.

Installs the redistributables a Windows game needs (VC++ runtimes,
d3dcompiler, mfc140, …) into its Proton prefix via
``umu-run winetricks <pkgs>``, exactly once per prefix (marker-guarded).
The package list comes from :mod:`game_fixes` — manual overrides →
umu-database protonfixes → global defaults. Generic: runs for every
Windows store, not just Epic.
"""
from __future__ import annotations

import logging
from pathlib import Path

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.fixes.game_fixes import get_required_winetricks
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import (
    run_umu_with_retry,
)

logger = logging.getLogger(__name__)

_MARKER_NAME = "unifideck_winetricks_complete.marker"
# Marker bodies that mean "don't run again" (terminal states).
_TERMINAL_MARKERS = ("complete", "no redistributables", "failed")


def _prefix_root(plan: ProtonLaunchPlan) -> Path:
    """Resolve the prefix root (strip a trailing ``pfx`` segment)."""
    p = plan.prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p


def _already_done(marker: Path) -> bool:
    """True if a prior run reached a terminal state for this prefix."""
    if not marker.is_file():
        return False
    try:
        body = marker.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(m in body for m in _TERMINAL_MARKERS)


def _write_marker(marker: Path, body: str) -> None:
    """Best-effort marker write."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(body, encoding="utf-8")
    except OSError as e:
        logger.debug("[compat.winetricks] marker write failed: %s", e)


async def apply_winetricks(plan: ProtonLaunchPlan) -> None:
    """Install required redistributables once per prefix.

    Best-effort: any failure writes a ``failed`` marker and returns —
    the caller continues to launch the game regardless.
    """
    prefix_root = _prefix_root(plan)
    marker = prefix_root / _MARKER_NAME
    if _already_done(marker):
        logger.debug(
            "[compat.winetricks] already done for %s", prefix_root,
        )
        return

    packages = await get_required_winetricks(plan.context.game_id)
    if not packages:
        _write_marker(marker, "no redistributables")
        return

    logger.info(
        "[compat.winetricks] installing for %s: %s",
        plan.context.game_id, ", ".join(packages),
    )
    _write_marker(marker, "installing: " + ", ".join(packages))
    launcher_toast(
        "toasts.launcher.installingRedistributables",
        i18n_title_key="toasts.launcher.dependenciesTitle",
        game_title=plan.context.game_key,
    )

    # winetricks runs under the same Proton/prefix the game uses. umu's
    # GAMEID=umu-0 (generic, no per-game protonfix) + no runtime update
    # so the redistributable install doesn't churn the umu runtime.
    env = dict(plan.env)
    env["WINEPREFIX"] = str(prefix_root)
    env["GAMEID"] = "umu-0"
    env["UMU_RUNTIME_UPDATE"] = "0"
    argv = [
        str(plan.python_bin),
        str(plan.umu_wrapper),
        "winetricks",
        "-q",  # unattended — never block on a GUI prompt
        *packages,
    ]
    try:
        rc = await run_umu_with_retry(argv, env=env)
    except Exception:
        logger.exception("[compat.winetricks] run failed")
        _write_marker(marker, "failed: exception")
        return
    if rc == 0:
        _write_marker(marker, "complete")
        logger.info(
            "[compat.winetricks] complete for %s", plan.context.game_id,
        )
        launcher_toast(
            "toasts.launcher.redistributablesInstalled",
            i18n_title_key="toasts.launcher.dependenciesReady",
            game_title=plan.context.game_key,
        )
    else:
        _write_marker(marker, f"failed: exit {rc}")
        logger.warning(
            "[compat.winetricks] rc=%d for %s",
            rc, plan.context.game_id,
        )
        launcher_toast(
            "toasts.launcher.checkLogs",
            i18n_title_key="toasts.launcher.dependenciesStatus",
            game_title=plan.context.game_key,
            severity="warning",
        )
