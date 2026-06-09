"""compat/prefix_init.py — synchronous prefix creation + proton-change reset.

Runs once at the start of every Windows launch, before the compat
steps and the game. Two jobs (ported from staging's launcher
prefix-init block):

1. **Proton-change reset.** Each prefix records the Proton tool that
   built it (``.unifideck_proton_version``). When the resolved Proton
   *family* changes — the user switched Force Compatibility, unselected
   it (falling back to the latest GE-Proton), or moved between
   Experimental ↔ GE ↔ Proton 9/10 — the old Wine prefix is
   incompatible, so we back up its user data, wipe it + the setup
   markers, and toast ``protonUpgrade``/``resettingPrefix``. A
   same-family bump (e.g. GE-Proton10-10 → 10-34) keeps the prefix
   (Proton upgrades it in place) and just toasts ``protonSwitchedTo``.

2. **First-time init.** If the prefix has no ``system.reg`` it isn't a
   usable Wine prefix yet, so we run ``umu-run createprefix`` (with
   retry) and toast ``firstTimeSetup``/``initializingPrefix`` →
   ``setupCompleteTitle``/``prefixInitialized``, falling back to
   ``wineboot --init`` (``setupFallback``) if createprefix doesn't
   produce a ``system.reg``.

Entirely best-effort: any failure is logged and the launch proceeds
(the game's own first umu run still initialises the prefix, exactly as
before this step existed).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.infrastructure.prefix_layout import (
    normalize_prefix_root,
    resolve_registry_prefix,
)
from unifideck.launcher.proton.infrastructure.umu_runtime import (
    cleanup_umu_runtime_cache,
    ensure_umu_runtime_ready,
)

if TYPE_CHECKING:
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

_MARKER_NAME = ".unifideck_proton_version"
# Kept across a prefix reset — everything else under the prefix root is
# Wine/Proton state or a re-runnable setup marker and gets wiped.
_PRESERVE = frozenset({_MARKER_NAME, ".save_backup"})
_CREATEPREFIX_ATTEMPTS = 3
_CREATEPREFIX_BACKOFF_SECONDS = 5


def _proton_family(tool_id: str) -> str:
    """Coarse Proton family — a change here means the prefix must reset."""
    t = tool_id.lower()
    if "experimental" in t:
        return "experimental"
    if "ge-proton" in t:
        return "ge-proton"
    if "umu-proton" in t:
        return "umu-proton"
    if "proton9" in t or "proton_9" in t or "proton 9" in t or "9.0" in t:
        return "proton9"
    if "proton10" in t or "proton_10" in t or "proton 10" in t or "10.0" in t:
        return "proton10"
    return "other"


async def ensure_prefix_initialized(plan: ProtonLaunchPlan) -> None:
    """Reset the prefix on a Proton family change, then create it if new."""
    try:
        prefix_root = normalize_prefix_root(plan.prefix_path)
        current = plan.state.proton_tool_id or "default"
        _handle_proton_change(plan, prefix_root, current)
        await _ensure_created(plan, prefix_root)
    except Exception:
        logger.exception("[prefix_init] prefix init/reset failed (non-fatal)")


def _read_previous_proton(prefix_root: Path) -> str | None:
    """The Proton tool that last built this prefix (our marker only).

    Deliberately does NOT fall back to Proton's own ``version`` file:
    on rollout, prefixes created before this feature have no marker, and
    we must not mass-reset working prefixes just because their Proton
    family differs from the new default. A missing marker → treat as a
    fresh baseline (record current, don't reset).
    """
    marker = prefix_root / _MARKER_NAME
    if not marker.is_file():
        return None
    try:
        return marker.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


def _handle_proton_change(
    plan: ProtonLaunchPlan, prefix_root: Path, current: str,
) -> None:
    """Reset (major change) or notify (minor change); update the marker."""
    previous = _read_previous_proton(prefix_root)
    if previous and previous != current:
        if _proton_family(previous) != _proton_family(current):
            logger.info(
                "[prefix_init] Proton family change %s -> %s; resetting prefix",
                previous, current,
            )
            launcher_toast(
                "toasts.launcher.resettingPrefix",
                i18n_title_key="toasts.launcher.protonUpgrade",
                i18n_params={"version": current},
                game_title=plan.context.game_key,
                severity="warning",
            )
            _reset_prefix(prefix_root)
        else:
            logger.info(
                "[prefix_init] minor Proton change %s -> %s; keeping prefix",
                previous, current,
            )
            launcher_toast(
                "toasts.launcher.protonSwitchedTo",
                i18n_title_key="toasts.launcher.protonUpgrade",
                i18n_params={"version": current},
                game_title=plan.context.game_key,
            )
    with contextlib.suppress(OSError):
        prefix_root.mkdir(parents=True, exist_ok=True)
        (prefix_root / _MARKER_NAME).write_text(current, encoding="utf-8")


def _reset_prefix(prefix_root: Path) -> None:
    """Back up user data, then wipe the prefix (keeping our markers)."""
    active = resolve_registry_prefix(prefix_root)
    users = active / "drive_c" / "users"
    backup = prefix_root / ".save_backup"
    with contextlib.suppress(OSError):
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if users.is_dir():
            shutil.copytree(users, backup, dirs_exist_ok=True)
    # Remove all Wine/Proton state + re-runnable setup markers, leaving
    # only the proton-version marker and the save backup behind.
    for entry in _safe_iterdir(prefix_root):
        if entry.name in _PRESERVE:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                entry.unlink()


def _safe_iterdir(path: Path) -> list[Path]:
    """``iterdir`` that returns ``[]`` instead of raising."""
    try:
        return list(path.iterdir())
    except OSError:
        return []


async def _ensure_created(plan: ProtonLaunchPlan, prefix_root: Path) -> None:
    """Run ``createprefix`` when the prefix has no ``system.reg`` yet."""
    if (prefix_root / "system.reg").is_file():
        logger.debug("[prefix_init] prefix already initialised: %s", prefix_root)
        return

    logger.info("[prefix_init] initialising prefix %s", prefix_root)
    launcher_toast(
        "toasts.launcher.initializingPrefix",
        i18n_title_key="toasts.launcher.firstTimeSetup",
        game_title=plan.context.game_key,
    )
    # ``_handle_proton_change`` (called just before) already created the
    # prefix root; umu createprefix populates the Wine tree inside it.
    ensure_umu_runtime_ready()
    env = dict(plan.env)
    env["GAMEID"] = "umu-0"  # generic — no per-game protonfix during setup

    if await _run_createprefix_with_retry(plan, env, prefix_root):
        launcher_toast(
            "toasts.launcher.prefixInitialized",
            i18n_title_key="toasts.launcher.setupCompleteTitle",
            game_title=plan.context.game_key,
        )
        return

    # Last resort — wineboot --init (createprefix never wrote system.reg).
    logger.warning("[prefix_init] createprefix failed; trying wineboot --init")
    launcher_toast(
        "toasts.launcher.fallbackInitialization",
        i18n_title_key="toasts.launcher.setupFallback",
        game_title=plan.context.game_key,
        severity="warning",
    )
    await _run_umu(plan, env, "wineboot", "--init")
    if (prefix_root / "system.reg").is_file():
        logger.info("[prefix_init] wineboot fallback initialised the prefix")
    else:
        logger.warning(
            "[prefix_init] prefix still missing system.reg — game may init it",
        )


async def _run_createprefix_with_retry(
    plan: ProtonLaunchPlan, env: dict[str, str], prefix_root: Path,
) -> bool:
    """Run ``umu-run createprefix`` until ``system.reg`` appears.

    Proton returns non-zero for ``createprefix`` even on success (it
    tries to "run" the keyword), so success is the presence of
    ``system.reg``, not the exit code.
    """
    wait = _CREATEPREFIX_BACKOFF_SECONDS
    for attempt in range(1, _CREATEPREFIX_ATTEMPTS + 1):
        logger.info(
            "[prefix_init] createprefix attempt %d/%d",
            attempt, _CREATEPREFIX_ATTEMPTS,
        )
        await _run_umu(plan, env, "createprefix")
        if (prefix_root / "system.reg").is_file():
            logger.info("[prefix_init] prefix created (system.reg present)")
            return True
        if attempt < _CREATEPREFIX_ATTEMPTS:
            launcher_toast(
                "toasts.launcher.retryingUmu",
                i18n_title_key="toasts.launcher.networkError",
                i18n_params={
                    "seconds": wait,
                    "attempt": attempt + 1,
                    "max": _CREATEPREFIX_ATTEMPTS,
                },
                severity="warning",
            )
            cleanup_umu_runtime_cache()
            await asyncio.sleep(wait)
            wait *= 2
    return False


async def _run_umu(
    plan: ProtonLaunchPlan, env: dict[str, str], *umu_args: str,
) -> None:
    """Spawn ``<python> <umu-run> <args>`` and wait (output discarded)."""
    argv = [str(plan.python_bin), str(plan.umu_wrapper), *umu_args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except OSError as e:
        logger.warning("[prefix_init] umu %s spawn failed: %s", umu_args, e)
