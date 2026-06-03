"""compat/vcruntime.py — VC++ runtime registry fix for UE4-class titles.

UE4 launcher stubs call ``MsiQueryProductState`` to verify VC++ is
installed; winetricks copies the DLLs but doesn't populate the MSI
product database, and Proton rewrites ``system.reg`` on prefix
upgrades — erasing text-injected keys. This imports a bundled ``.reg``
via ``umu-run regedit`` *after* Proton has initialised the prefix. The
marker is keyed to the Proton tool name so it re-runs when the user
switches Proton. Generic across stores; best-effort.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import (
    run_umu_with_retry,
)

logger = logging.getLogger(__name__)


def _prefix_root(plan: ProtonLaunchPlan) -> Path:
    """Resolve the prefix root (strip a trailing ``pfx`` segment)."""
    p = plan.prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p


def _find_drive_c(prefix_root: Path) -> Path | None:
    """Locate ``drive_c`` for either prefix layout, or None if absent."""
    for candidate in (prefix_root / "drive_c", prefix_root / "pfx" / "drive_c"):
        if candidate.is_dir():
            return candidate
    return None


async def apply_vcruntime_fix(plan: ProtonLaunchPlan) -> None:
    """Import the bundled VC++ runtime keys once per (prefix, Proton)."""
    reg_file = plan.context.plugin_dir / "bin" / "vcruntime_fix.reg"
    if not reg_file.is_file():
        return
    prefix_root = _prefix_root(plan)
    proton_name = plan.state.proton_tool_id or "unknown"
    marker = prefix_root / f".unifideck_vcreg_{proton_name}.done"
    if marker.is_file():
        return
    drive_c = _find_drive_c(prefix_root)
    if drive_c is None:
        # Prefix not initialised yet (first ever launch). The next
        # launch — after Proton creates drive_c — will apply it.
        logger.info("[compat.vcruntime] drive_c not ready, deferring")
        return

    staged = drive_c / "vcruntime_fix.reg"
    try:
        shutil.copy2(reg_file, staged)
    except OSError as e:
        logger.warning("[compat.vcruntime] staging copy failed: %s", e)
        return

    env = dict(plan.env)
    env["GAMEID"] = "umu-0"
    argv = [
        str(plan.python_bin),
        str(plan.umu_wrapper),
        "regedit",
        "C:\\vcruntime_fix.reg",
    ]
    rc = 1
    try:
        rc = await run_umu_with_retry(argv, env=env)
    except Exception:
        logger.exception("[compat.vcruntime] regedit run failed")
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()

    if rc == 0:
        # Drop stale markers from other Proton versions, write current.
        for old in prefix_root.glob(".unifideck_vcreg_*.done"):
            with contextlib.suppress(OSError):
                old.unlink()
        with contextlib.suppress(OSError):
            marker.write_text("done", encoding="utf-8")
        logger.info(
            "[compat.vcruntime] imported for proton=%s", proton_name,
        )
    else:
        logger.warning("[compat.vcruntime] regedit rc=%d", rc)
