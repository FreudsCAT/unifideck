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
    UMU_TIMEOUT_RC,
    run_umu_with_retry,
)

logger = logging.getLogger(__name__)

# Bounds the regedit import so a hung Proton/Wine (e.g. a broken
# auto-updated Proton-Experimental build spinning wineserver forever)
# can't wedge the serial install queue during prefix warmup. This is a
# trivial 4-key registry import that finishes in well under a second on
# a healthy prefix — a generous bound here only ever fires on a genuine
# hang. On timeout no ``.done`` marker is written (only rc==0 writes
# one), so a good Proton retries it next launch.
_VCRUNTIME_TIMEOUT_SECONDS = 120.0


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


async def apply_vcruntime_fix(plan: ProtonLaunchPlan) -> bool:
    """Import the bundled VC++ runtime keys once per (prefix, Proton).

    Returns ``True`` only when the umu regedit step was force-killed for
    exceeding its timeout (a hung Proton), so the warmup caller can
    retry with a good Proton. A ``.done`` marker is written only on a
    clean ``rc == 0``, so a timeout is naturally retried next launch.
    """
    reg_file = plan.context.plugin_dir / "bin" / "vcruntime_fix.reg"
    if not reg_file.is_file():
        return False
    prefix_root = _prefix_root(plan)
    proton_name = plan.state.proton_tool_id or "unknown"
    # ``.v2`` invalidates markers written by the earlier broken build,
    # which mistook regedit's "file not found" dialog (rc 0) for a
    # successful import — the keys were never actually applied.
    marker = prefix_root / f".unifideck_vcreg_{proton_name}.v2.done"
    if marker.is_file():
        return False

    env = dict(plan.env)
    env["GAMEID"] = "umu-0"
    # ``run``, not the inherited ``waitforexitandrun``: the latter's
    # ``wineserver -w`` deadlocks against a resident wineserver left by the
    # earlier createprefix/winetricks step. See prefix_init._ensure_created.
    env["PROTON_VERB"] = "run"
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
        rc = await run_umu_with_retry(
            argv, env=env, timeout=_VCRUNTIME_TIMEOUT_SECONDS,
        )
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
        return False
    if rc == UMU_TIMEOUT_RC:
        logger.warning(
            "[compat.vcruntime] regedit timed out (proton=%s hung) — "
            "no marker written, will retry with a good Proton",
            proton_name,
        )
        return True
    logger.warning("[compat.vcruntime] regedit rc=%d", rc)
    return False
