"""launcher.proton — Proton-based launch orchestration.

Public surface used by the dispatcher: handler functions per
store, the ``ProtonLaunchPlan`` dataclass, selector helpers for
finding the right Python / Proton version, and UMU-runtime
cache management.
"""

from __future__ import annotations

from .handlers.epic import epic_launch
from .handlers.generic import generic_launch
from .handlers.ubisoft import ubisoft_launch
from .infrastructure.core import ProtonLaunchPlan, proton_prepare
from .infrastructure.selector import (
    find_python_3_10_plus,
    resolve_proton_path,
    select_managed_ge_proton,
    select_proton_version,
)
from .infrastructure.umu_runtime import (
    UMU_CACHE_DIR,
    cleanup_umu_runtime_cache,
    ensure_umu_runtime_ready,
    repair_incomplete_umu_runtime,
    run_umu_with_retry,
)
from .prefix_setup import setup_prefix


async def dispatch(plan: ProtonLaunchPlan) -> int:
    """Dispatch.

    Routes the prepared plan to the per-store handler, which adds any
    store-specific compatibility (Epic EOS overlay, GOG galaxy stub, Amazon
    fuel args) and runs the game through umu-run.

    Prefix creation AND generic compat (createprefix + winetricks + VC++
    registry fix, with the managed-GE recovery ladder + pin) are NOT done
    here: the orchestrator runs the canonical :func:`setup_prefix` earlier
    (Phase 1.5), before the cloud sync-down, so the save dir resolves out of
    ``drive_c`` on the first launch and the exact same self-healing setup runs
    at launch as at install-time warmup. Running compat here too would
    double-run it (and its proton-change toast), so it lives in the single
    ``setup_prefix`` call in ``orchestrator.launch_windows``.
    """
    # Self-heal a half-downloaded umu runtime (payload present but the
    # umu/_v2-entry-point link missing) before anything spawns umu-run this
    # launch (UD-084). Store-agnostic, and a cheap no-op stat when healthy.
    repair_incomplete_umu_runtime()

    store = plan.context.store
    if store == "ubisoft":
        return await ubisoft_launch(plan)
    if store == "epic":
        return await epic_launch(plan)
    return await generic_launch(plan)


__all__ = [
    "UMU_CACHE_DIR",
    "ProtonLaunchPlan",
    "cleanup_umu_runtime_cache",
    "dispatch",
    "ensure_umu_runtime_ready",
    "epic_launch",
    "find_python_3_10_plus",
    "generic_launch",
    "proton_prepare",
    "repair_incomplete_umu_runtime",
    "resolve_proton_path",
    "run_umu_with_retry",
    "select_managed_ge_proton",
    "select_proton_version",
    "setup_prefix",
    "ubisoft_launch",
]
