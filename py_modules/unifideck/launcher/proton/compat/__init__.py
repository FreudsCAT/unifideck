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

**Ubisoft is skipped entirely** — its games launch *through* Ubisoft
Connect (UPC), which installs the redistributables (VC++ runtimes, …) it
and the game need as part of the install. Running our generic winetricks
step on top is redundant and added a ~90s first-launch delay reinstalling
what UPC already provides. The per-game prefix (cloned from the UPC
template) is all that's required.

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


async def apply_prefix_compat(plan: ProtonLaunchPlan) -> bool:
    """Run generic per-prefix compatibility setup for a Windows game.

    winetricks first (installs the redistributables), then the VC++
    registry fix (which assumes those DLLs are present). Each step is
    independently guarded so one failure doesn't skip the other or the
    launch.

    Returns ``True`` if any step was force-killed for exceeding its
    timeout — the signal that the selected Proton hung at runtime (a
    structurally-complete but broken build the static install check
    can't catch). The install-time warmup uses this to retry once with
    the managed GE-Proton; the launch path ignores it.
    """
    # Ubisoft games launch through UPC, which installs its own
    # redistributables — our generic winetricks/vcredist step is redundant
    # and only adds a first-launch delay. The cloned per-game prefix +
    # UPC are all that's needed, so skip generic compat entirely.
    if plan.context.store == "ubisoft":
        logger.info(
            "[compat] skipping generic redistributables for ubisoft "
            "(UPC installs its own)",
        )
        return False
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
        return False

    timed_out = False
    for label, step in (
        ("winetricks", apply_winetricks),
        ("vcruntime", apply_vcruntime_fix),
    ):
        try:
            if await step(plan):
                timed_out = True
        except Exception:
            logger.exception(
                "[compat] %s step failed (continuing to launch)", label,
            )
    return timed_out


__all__ = ["apply_prefix_compat", "apply_vcruntime_fix", "apply_winetricks"]
