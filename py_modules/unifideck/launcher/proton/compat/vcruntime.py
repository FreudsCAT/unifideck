"""compat/vcruntime.py — VC++ runtime registry fix for UE4-class titles.

UE4 launcher stubs call ``MsiQueryProductState`` to verify VC++ is
installed; winetricks copies the DLLs but doesn't populate the MSI
product database, and Proton rewrites ``system.reg`` on prefix
upgrades — erasing text-injected keys. This imports a bundled ``.reg``
via ``umu-run regedit`` *after* Proton has initialised the prefix
(winetricks runs first in :mod:`compat`). The marker is keyed to the
Proton tool name so it re-runs when the user switches Proton. Generic
across stores; best-effort.
"""
from __future__ import annotations

import contextlib
import logging
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


def _wine_z_path(linux_path: Path) -> str:
    """Map an absolute Linux path to its Wine ``Z:`` drive path.

    Wine always maps ``Z:\\`` to ``/``, so we can hand regedit the
    bundled .reg directly — no copy into ``drive_c`` and no guessing
    which prefix layout umu used for ``C:`` (the bug that produced
    ``regedit: The file 'C:\\vcruntime_fix.reg' was not found``).
    """
    return "Z:" + str(linux_path).replace("/", "\\")


async def apply_vcruntime_fix(plan: ProtonLaunchPlan) -> None:
    """Import the bundled VC++ runtime keys once per (prefix, Proton)."""
    reg_file = plan.context.plugin_dir / "bin" / "vcruntime_fix.reg"
    if not reg_file.is_file():
        return
    prefix_root = _prefix_root(plan)
    proton_name = plan.state.proton_tool_id or "unknown"
    # ``.v2`` invalidates markers written by the earlier broken build,
    # which mistook regedit's "file not found" dialog (rc 0) for a
    # successful import — the keys were never actually applied.
    marker = prefix_root / f".unifideck_vcreg_{proton_name}.v2.done"
    if marker.is_file():
        return

    env = dict(plan.env)
    env["GAMEID"] = "umu-0"
    # ``/S`` imports silently — no GUI dialog on success OR error. The
    # error dialog (when C: path was wrong) blocked the launch for as
    # long as it stayed open; the Z: path + /S removes that entirely.
    argv = [
        str(plan.python_bin),
        str(plan.umu_wrapper),
        "regedit",
        "/S",
        _wine_z_path(reg_file),
    ]
    rc = 1
    try:
        rc = await run_umu_with_retry(argv, env=env)
    except Exception:
        logger.exception("[compat.vcruntime] regedit run failed")

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
