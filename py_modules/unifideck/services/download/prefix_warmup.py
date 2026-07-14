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
import os
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


def _reap_stale_prefix_wineserver(game_id: str) -> None:
    """Reap an orphaned wineserver on this game's prefix, if any.

    Best-effort pre-warmup hygiene (see call site). Non-Ubisoft install
    prefixes live at ``~/.local/share/unifideck/prefixes/<game_id>``; that
    is what the setup steps below build/use, so it's the server dir to
    clear. Any failure is swallowed — a broken reap must never block setup.
    """
    try:
        from unifideck.launcher.proton.infrastructure.wineserver_reap import (
            reap_prefix_wineserver,
        )
        prefix = Path(
            "~/.local/share/unifideck/prefixes",
        ).expanduser() / game_id
        if prefix.exists():
            reap_prefix_wineserver(prefix)
    except Exception:
        logger.exception(
            "[prefix_warmup] pre-warmup wineserver reap failed for %s", game_id,
        )


# Session vars the install-time umu runs need. The Decky backend is a
# headless service (spawned by plugin_loader) whose environment carries NO
# user session — none of these four are set there. winetricks/vcredist under
# ntsync-era Proton builds (GE-Proton11+, Proton Exp 2026-07) hangs
# indefinitely or fails in that context, which wedged the serial install
# queue on every fresh-prefix install. Proven by A/B on-device: backend env →
# rc=1 / indefinite hang at vcredist_x86.EXE; backend env + these four vars →
# rc=0 in ~55s. At launch time Steam provides them to the launcher (why the
# same winetricks always worked at launch, incl. all of 0.6.1); at install
# time we borrow them from the running Steam client so warmup matches launch.
_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


def _session_env_from_environ(data: bytes) -> dict[str, str]:
    """Extract the session vars from a ``/proc/<pid>/environ`` blob."""
    found: dict[str, str] = {}
    for chunk in data.split(b"\0"):
        key, sep, value = chunk.partition(b"=")
        if not sep or not value:
            continue
        try:
            name = key.decode()
        except UnicodeDecodeError:
            continue
        if name in _SESSION_ENV_KEYS:
            found[name] = value.decode(errors="replace")
    return found


def _user_session_env() -> dict[str, str]:
    """Best-effort user-session env for install-time umu runs.

    Borrows the four session vars from the running Steam client (same uid) —
    exactly the environment the launcher inherits at launch time. Falls back
    to the deterministic ``/run/user/<uid>`` locations when Steam isn't up.
    Returns only the vars it could resolve; callers merge with ``setdefault``
    so a launch-provided value is never clobbered.
    """
    found: dict[str, str] = {}
    uid = os.getuid()
    try:
        proc_entries = os.listdir("/proc")
    except OSError:
        proc_entries = []
    for entry in proc_entries:
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid:
                continue
            with open(f"/proc/{entry}/comm") as fh:
                if fh.read().strip() != "steam":
                    continue
            data = Path(f"/proc/{entry}/environ").read_bytes()
        except OSError:
            continue
        found = _session_env_from_environ(data)
        if found:
            break
    runtime = found.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    if os.path.isdir(runtime):
        found.setdefault("XDG_RUNTIME_DIR", runtime)
        if os.path.exists(f"{runtime}/bus"):
            found.setdefault(
                "DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus",
            )
    return found


async def _run_setup(ctx: Any, state: Any, python_bin: Any, proton: Any, key: str) -> bool:
    """createprefix + generic compat for one Proton; True if a step timed out.

    Best-effort: any failure is logged and swallowed (the launch-time path
    remains the fallback). ``proton`` is a ``(path, tool_id)`` pair.
    """
    from unifideck.launcher.proton import proton_prepare
    from unifideck.launcher.proton.compat import apply_prefix_compat
    from unifideck.launcher.proton.compat.prefix_init import (
        ensure_prefix_initialized,
    )

    proton_path, proton_tool_id = proton
    plan = proton_prepare(
        ctx, state, python_bin=python_bin,
        proton_path=proton_path, proton_tool_id=proton_tool_id,
    )
    # Install-time runs from the headless backend: graft the user session
    # env in (see _SESSION_ENV_KEYS above) so winetricks/vcredist behave
    # exactly as they do at launch under Steam.
    session_env = _user_session_env()
    for env_key, env_val in session_env.items():
        plan.env.setdefault(env_key, env_val)
    logger.info(
        "[prefix_warmup] session env grafted for %s: %s",
        key, ", ".join(sorted(session_env)) or "none-resolved",
    )
    try:
        await ensure_prefix_initialized(plan)
        return await apply_prefix_compat(plan)
    except Exception:
        logger.exception(
            "[prefix_warmup] prefix init/compat failed for %s (continuing)", key,
        )
        return False


async def _setup_prefix_with_ge_retry(ctx: Any, state: Any, key: str) -> None:
    """Run prefix init+compat, retrying once with managed GE on a hang.

    A compat step being force-killed for exceeding its timeout means the
    selected Proton hung at runtime. Recovery ladder, gated so we never loop:

      1. Setup under the default Proton.
      2. On hang → switch to the plugin-managed GE-Proton and retry.

    All best-effort: if every attempt still times out, the prefix finishes
    at first launch (the launch path re-runs the same steps).

    (An earlier revision inserted a repair-in-place step here — official
    Protons via ``SteamClient.Apps.VerifyApp``, GE via re-install — before
    this retry. Removed: across every hang it fired on, VerifyApp reported
    success but the same-Proton retry hung again regardless, so it added a
    round trip without ever changing the outcome. The actual cause was a
    missing user-session env for install-time umu runs, fixed below in
    ``_user_session_env``; see git history / memory for the VerifyApp
    mechanism if a genuinely-corrupt-Proton case ever needs it.)
    """
    from unifideck.launcher.proton import (
        find_python_3_10_plus,
        select_managed_ge_proton,
        select_proton_version,
    )

    python_bin = find_python_3_10_plus()
    # No per-game Force-Compat choice exists yet (no shortcut/steam_app_id at
    # install), so this resolves the same default the first launch picks.
    _, default_tool = default_proton = select_proton_version(
        steam_app_id=None, store_game_id=key,
    )
    if not await _run_setup(ctx, state, python_bin, default_proton, key):
        return

    ge_path, ge_tool = select_managed_ge_proton()
    if ge_tool == default_tool:
        logger.warning(
            "[prefix_warmup] compat timed out for %s under managed GE-Proton "
            "%s — not retrying (prefix finishes at launch)", key, ge_tool,
        )
        return
    logger.warning(
        "[prefix_warmup] compat still timing out for %s under proton=%s — "
        "retrying setup with managed GE-Proton %s", key, default_tool, ge_tool,
    )
    await _run_setup(ctx, state, python_bin, (ge_path, ge_tool), key)


async def warmup_install_prefix(
    store: str,
    game_id: str,
    install_path: str,
    *,
    cloud_svc: Any = None,
) -> None:
    """Run the full first-run prefix setup (+ cloud pull) at install time."""
    from unifideck.launcher.frontend_bridge import suppress_launcher_toasts

    key = f"{store}:{game_id}"
    logger.info("[prefix_warmup] starting install-time prefix setup for %s", key)

    # Pre-warmup hygiene: reap any wineserver still holding THIS game's
    # prefix server dir, left orphaned by a prior hung/timed-out setup
    # attempt. Without this, the fresh createprefix below deadlocks against
    # the orphan on the shared /tmp/.wine-<uid>/server-<dev>-<ino>/lock (the
    # observed "install keeps getting stuck" wedge). Surgical — only this
    # prefix's server dir, never a wineserver for a game the user is running.
    _reap_stale_prefix_wineserver(game_id)

    ctx, state = _build_launch_context(store, game_id, Path(install_path))

    # Steps 1+2 reuse the launch path's prefix-init / compat / GE-download /
    # umu-runtime helpers, all of which toast launch progress. During a
    # background install those toasts are noise (the download row shows
    # "Setting up game…"), so suppress them for the whole setup block.
    with suppress_launcher_toasts():
        await _setup_prefix_with_ge_retry(ctx, state, key)

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
