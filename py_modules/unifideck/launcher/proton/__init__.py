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
    run_umu_with_retry,
)


async def dispatch(plan: ProtonLaunchPlan) -> int:
    """Dispatch.

    Runs the store-agnostic per-prefix compatibility setup
    (redistributables + VC++ registry fix) once, then routes to the
    per-store handler which adds any store-specific compatibility
    (Epic EOS overlay, GOG galaxy stub, Amazon fuel args).

    Prefix creation / Proton-change reset (``ensure_prefix_initialized``)
    is NOT done here: the orchestrator runs it earlier (Phase 1.5), before
    the cloud sync-down, so the save dir resolves out of ``drive_c`` on the
    first launch. Calling it again here would double-run the proton-change
    toast, so the single call lives in ``orchestrator.launch_windows``.
    """
    from .compat import apply_prefix_compat

    await apply_prefix_compat(plan)

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
    "resolve_proton_path",
    "run_umu_with_retry",
    "select_managed_ge_proton",
    "select_proton_version",
    "ubisoft_launch",
]
