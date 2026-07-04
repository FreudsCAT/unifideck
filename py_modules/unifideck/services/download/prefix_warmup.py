"""services/download/prefix_warmup.py — install-time prefix initialisation.

Historically the Wine prefix was created lazily on the *first* game launch
(``ensure_prefix_initialized`` inside ``proton.dispatch``). That left a window
where the on-launch cloud-save sync-down ran *before* the prefix existed: the
save dir resolves out of ``drive_c`` (e.g. GOG's ``<?DOCUMENTS?>\\<title>``),
which isn't there until ``createprefix`` has run — so the first launch pulled no
saves and the user only saw them after a relaunch.

This module runs the SAME first-run setup eagerly at install time, for the
download stores that own a per-game prefix (Epic / GOG / Amazon — NOT Ubisoft,
which bootstraps its own prefix via UPC, nor Microsoft, which is cloud-only):

  1. ``ensure_prefix_initialized`` — ``umu-run createprefix`` (+ save migrate).
  2. ``apply_prefix_compat`` — winetricks redistributables + VC++ registry fix.
  3. ``CloudSaveService.sync_down`` — pull cloud saves now that ``drive_c`` exists.

Reuses the launch machinery (``proton_prepare`` etc.) by building a
``LaunchContext``/``ProtonLaunchPlan`` outside an actual launch. Every step is
idempotent (createprefix is skipped once ``system.reg`` exists, compat steps are
marker-guarded, gogdl skips an already-synced timestamp), so the launch-time
path re-running them later is a cheap no-op. Best-effort throughout: any failure
is logged and the install still completes — the launch-time path remains the
fallback.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import DownloadItem

logger = logging.getLogger(__name__)


def _build_launch_context(store: str, game_id: str, install: Path) -> Any:
    """Construct the ``(ctx, state)`` pair the setup steps run against.

    Builds a ``LaunchContext``/``RuntimeState`` outside an actual launch so
    the prefix-init / compat helpers can be reused at install time.
    """
    from unifideck.core.paths import resolve_plugin_dir
    from unifideck.launcher.types.context import LaunchContext, RuntimeState

    ctx = LaunchContext(
        store=store,
        game_id=game_id,
        # exe_path is unused by the setup steps (they key off WINEPREFIX); the
        # install dir is a harmless, valid Path placeholder.
        exe_path=install,
        work_dir=install,
        plugin_dir=resolve_plugin_dir(start=Path(__file__)),
        steam_app_id=None,
    )
    return ctx, RuntimeState()


async def warmup_install_prefix(
    store: str,
    game_id: str,
    install_path: str,
    *,
    cloud_svc: Any = None,
) -> None:
    """Run the full first-run prefix setup (+ cloud pull) at install time."""
    from unifideck.launcher.frontend_bridge import suppress_launcher_toasts
    from unifideck.launcher.proton import (
        find_python_3_10_plus,
        proton_prepare,
        select_proton_version,
    )
    from unifideck.launcher.proton.compat import apply_prefix_compat
    from unifideck.launcher.proton.compat.prefix_init import (
        ensure_prefix_initialized,
    )

    key = f"{store}:{game_id}"
    logger.info("[prefix_warmup] starting install-time prefix setup for %s", key)

    ctx, state = _build_launch_context(store, game_id, Path(install_path))

    # Steps 1+2 reuse the launch path's prefix-init / compat / GE-download /
    # umu-runtime helpers, all of which toast launch progress. During a
    # background install those toasts are noise (the download row shows
    # "Setting up game…"), so suppress them for the whole setup block.
    with suppress_launcher_toasts():
        python_bin = find_python_3_10_plus()
        # No per-game Force-Compat choice exists yet (no shortcut/steam_app_id
        # at install), so this resolves the same default the first launch picks.
        proton_path, proton_tool_id = select_proton_version(
            steam_app_id=None, store_game_id=key,
        )
        plan = proton_prepare(
            ctx,
            state,
            python_bin=python_bin,
            proton_path=proton_path,
            proton_tool_id=proton_tool_id,
        )

        # Create the prefix and run the generic compat setup. Both are
        # individually best-effort internally, but guard here too so a failure
        # in one never skips the cloud pull below.
        try:
            await ensure_prefix_initialized(plan)
            await apply_prefix_compat(plan)
        except Exception:
            logger.exception(
                "[prefix_warmup] prefix init/compat failed for %s (continuing)",
                key,
            )

    # 3: pull cloud saves now that drive_c exists. The user opted into pulling
    # at install (in addition to the on-launch pull). Never fatal — a missing
    # store auth / network blip must not fail the install.
    if cloud_svc is not None:
        try:
            await cloud_svc.sync_down(store, game_id)
        except Exception:
            logger.exception(
                "[prefix_warmup] cloud sync_down failed for %s (non-fatal)", key,
            )

    logger.info("[prefix_warmup] finished install-time prefix setup for %s", key)


def make_prefix_warmup(
    cloud_svc: Any = None,
) -> Callable[[DownloadItem], Awaitable[None]]:
    """Build the download-worker hook bound to the cloud-save service.

    Returns a coroutine that takes the completed ``DownloadItem`` — the shape
    the worker's ``_prefix_warmup`` hook expects. The store-exclusion
    (Ubisoft / Microsoft) is enforced by the worker before this runs.
    """
    async def _warmup(item: DownloadItem) -> None:
        await warmup_install_prefix(
            item.store, item.game_id, item.install_path, cloud_svc=cloud_svc,
        )

    return _warmup
