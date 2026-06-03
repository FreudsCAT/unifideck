"""launcher.proton.compat — per-prefix compatibility setup.

A single :func:`apply_prefix_compat` entry, run once before every
Windows game launches (from ``proton.dispatch``), performing the
store-agnostic prefix preparation that any Windows title may need:

* redistributables (winetricks: VC++ runtimes, d3dcompiler, …)
* the VC++ runtime registry fix (UE4 ``MsiQueryProductState``)

Store-specific compatibility lives alongside but is invoked from the
per-store handlers, not here:

* Epic   → :mod:`compat.epic` (EOS overlay, config path, offline)
* GOG    → galaxy stub (``fixes.galaxy_stub``)
* Amazon → fuel.json args (handler)

Every step is first-launch only (marker-guarded) and best-effort — a
failure logs and never blocks the launch.
"""
from __future__ import annotations

import logging

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

from .vcruntime import apply_vcruntime_fix
from .winetricks import apply_winetricks

logger = logging.getLogger(__name__)


async def apply_prefix_compat(plan: ProtonLaunchPlan) -> None:
    """Run generic per-prefix compatibility setup for a Windows game.

    winetricks first (installs the redistributables), then the VC++
    registry fix (which assumes those DLLs are present). Each step is
    independently guarded so one failure doesn't skip the other or the
    launch.
    """
    for label, step in (
        ("winetricks", apply_winetricks),
        ("vcruntime", apply_vcruntime_fix),
    ):
        try:
            await step(plan)
        except Exception:
            logger.exception(
                "[compat] %s step failed (continuing to launch)", label,
            )


__all__ = ["apply_prefix_compat", "apply_vcruntime_fix", "apply_winetricks"]
