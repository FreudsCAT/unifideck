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
from unifideck.launcher.proton.infrastructure.prefix_layout import (
    normalize_prefix_root,
)

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
    # No initialised prefix (``createprefix`` hasn't produced ``system.reg``)
    # → there is nothing to install redistributables into. Skip rather than
    # let the steps run and write their terminal "done" markers anyway: a
    # bogus marker would suppress the REAL install on the next launch (this is
    # how the failed install-time warmup left prefixes with a "complete"
    # winetricks marker but no actual redistributables).
    prefix_root = normalize_prefix_root(plan.prefix_path)
    if not (prefix_root / "system.reg").is_file():
        logger.warning(
            "[compat] no system.reg at %s — skipping compat "
            "(prefix not initialised; markers left unwritten so launch redoes it)",
            prefix_root,
        )
        return

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
