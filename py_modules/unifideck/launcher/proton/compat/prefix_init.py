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
import os
import shutil
import signal
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.proton.compat.ge_fallback import fallback_to_ge_proton
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
# Bounds a single umu-run step (createprefix / wineboot --init). Generous —
# legitimate first-time setup downloads the Steam Linux Runtime (hundreds of
# MB) — but finite: a hung Proton/Wine boot (confirmed live: a broken
# Proton-Experimental build spinning wineserver forever) must be killed
# rather than orphaned to run indefinitely.
_UMU_STEP_TIMEOUT_SECONDS = 120.0
# Written into a per-game prefix once the one-time legacy-save migration
# has run, so we don't rescan the legacy umu prefixes on every launch.
_LEGACY_MIGRATED_MARKER = ".unifideck_legacy_migrated"
# Shared umu prefixes used before 0.6 set a per-game WINEPREFIX. Games
# launched then wrote their saves into umu's default prefix; we pull
# those forward on the first per-game prefix init.
_LEGACY_UMU_BASE = "~/Games/umu"
_LEGACY_UMU_SHARED = ("umu-0", "umu-default")


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


# Compat completion markers written by the per-prefix compat steps
# (compat/winetricks.py, compat/vcruntime.py). Cleared on a fresh
# createprefix so a stale one (from a failed setup) can't suppress the real
# install. The vcruntime marker is Proton-version-suffixed, so match by prefix.
_WINETRICKS_MARKER = "unifideck_winetricks_complete.marker"
_VCREG_MARKER_PREFIX = ".unifideck_vcreg_"


def _clear_stale_compat_markers(prefix_root: Path) -> None:
    """Delete compat 'done' markers (best-effort) before a fresh prefix build."""
    targets = [prefix_root / _WINETRICKS_MARKER]
    targets += [
        p for p in _safe_iterdir(prefix_root)
        if p.name.startswith(_VCREG_MARKER_PREFIX) and p.name.endswith(".done")
    ]
    for marker in targets:
        if marker.exists():
            with contextlib.suppress(OSError):
                marker.unlink()
                logger.info("[prefix_init] cleared stale compat marker %s", marker.name)


# ── save migration / restore ──────────────────────────────────────


def _merge_users(src_users: Path, dst_users: Path) -> int:
    """Copy files from ``src_users`` into ``dst_users``, non-destructively.

    A file is copied only when the destination is missing or older than
    the source (mtime guard), so a save written after a reset is never
    clobbered by a stale backup, and the merge is safe to re-run. Per-
    file errors are logged and skipped — best-effort, like the rest of
    this module. Returns the number of files actually copied.
    """
    if not src_users.is_dir():
        return 0
    copied = 0
    for src in src_users.rglob("*"):
        if not src.is_file():
            continue
        try:
            rel = src.relative_to(src_users)
            dst = dst_users / rel
            if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        except OSError as e:
            logger.warning("[prefix_init] save merge skipped %s: %s", src, e)
    return copied


def _users_has_files(users_dir: Path) -> bool:
    """True if ``users_dir`` holds at least one regular file."""
    if not users_dir.is_dir():
        return False
    try:
        return any(p.is_file() for p in users_dir.rglob("*"))
    except OSError:
        return False


def _restore_save_backup(prefix_root: Path) -> None:
    """Merge a prior reset's ``.save_backup`` into the live prefix.

    ``_reset_prefix`` copies ``drive_c/users`` to ``.save_backup`` before
    wiping the prefix but nothing used to put it back, so a Proton-family
    change silently lost saves. We restore it after the prefix is
    recreated. The backup is left in place — the mtime-guarded merge
    makes a repeat harmless and the next reset refreshes it.
    """
    backup = prefix_root / ".save_backup"
    if not backup.is_dir():
        return
    dst_users = resolve_registry_prefix(prefix_root) / "drive_c" / "users"
    copied = _merge_users(backup, dst_users)
    if copied:
        logger.info(
            "[prefix_init] restored %d save file(s) from .save_backup", copied,
        )


def _legacy_prefix_candidates(plan: ProtonLaunchPlan) -> list[Path]:
    """Legacy shared-umu prefixes that may hold this game's old saves."""
    base = Path(_LEGACY_UMU_BASE).expanduser()
    candidates: list[Path] = []
    game_gameid = (plan.env or {}).get("GAMEID")
    if game_gameid:
        # Old launchers that set a per-game GAMEID but no WINEPREFIX.
        candidates.append(base / game_gameid)
    candidates.extend(base / name for name in _LEGACY_UMU_SHARED)
    return candidates


def _migrate_legacy_prefix(plan: ProtonLaunchPlan, prefix_root: Path) -> None:
    """One-time: pull saves from a legacy shared umu prefix into this one.

    Pre-0.6 launches didn't set ``WINEPREFIX``, so games ran in umu's
    shared default prefix (``~/Games/umu/umu-0``). After upgrading, the
    new per-game prefix is empty and saves look lost. Copy the legacy
    ``drive_c/users`` tree forward (first candidate with real data wins),
    leaving the legacy prefix untouched so other games can migrate from
    it too. Idempotent via a per-prefix marker.
    """
    marker = prefix_root / _LEGACY_MIGRATED_MARKER
    if marker.exists():
        return
    dst_users = resolve_registry_prefix(prefix_root) / "drive_c" / "users"
    for candidate in _legacy_prefix_candidates(plan):
        src_users = resolve_registry_prefix(candidate) / "drive_c" / "users"
        if not _users_has_files(src_users):
            continue
        copied = _merge_users(src_users, dst_users)
        logger.info(
            "[prefix_init] migrated %d save file(s) from legacy prefix %s",
            copied, candidate,
        )
        break
    # Mark done even when nothing matched so we don't rescan every launch;
    # the merge is mtime-guarded, so a future re-run would be harmless.
    with contextlib.suppress(OSError):
        marker.write_text("done", encoding="utf-8")


async def _restore_or_migrate_saves(
    plan: ProtonLaunchPlan, prefix_root: Path,
) -> None:
    """After a fresh prefix is created, bring prior saves forward.

    A reset's ``.save_backup`` (this exact prefix's own data) is the most
    specific source and wins; otherwise fall back to a one-time legacy
    shared-prefix migration. Runs the blocking copy off the event loop.
    """
    if (prefix_root / ".save_backup").is_dir():
        await asyncio.to_thread(_restore_save_backup, prefix_root)
    else:
        await asyncio.to_thread(_migrate_legacy_prefix, plan, prefix_root)


async def _ensure_created(plan: ProtonLaunchPlan, prefix_root: Path) -> None:
    """Run ``createprefix`` when the prefix has no ``system.reg`` yet."""
    if (resolve_registry_prefix(prefix_root) / "system.reg").is_file():
        logger.debug("[prefix_init] prefix already initialised: %s", prefix_root)
        return

    # Reaching here means there's no system.reg, so we're (re)building the
    # prefix from scratch — any compat "done" markers present are stale (left
    # by a prior *failed* attempt, e.g. the install-time warmup that crashed
    # before the loader-env fix and wrote bogus "complete" markers). Clear them
    # so apply_prefix_compat actually re-installs the redistributables.
    _clear_stale_compat_markers(prefix_root)

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
        await _restore_or_migrate_saves(plan, prefix_root)
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
    if (resolve_registry_prefix(prefix_root) / "system.reg").is_file():
        logger.info("[prefix_init] wineboot fallback initialised the prefix")
        await _restore_or_migrate_saves(plan, prefix_root)
        return

    logger.warning(
        "[prefix_init] prefix still missing system.reg after createprefix "
        "+ wineboot fallback",
    )
    await fallback_to_ge_proton(plan, prefix_root)


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
        if (resolve_registry_prefix(prefix_root) / "system.reg").is_file():
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


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Best-effort SIGKILL of ``proc``'s whole process group.

    ``start_new_session=True`` makes the spawned umu-run its own
    session/process-group leader, so killing just ``proc.pid`` would
    leave every descendant running untouched — pressure-vessel,
    wineserver, the simulated services.exe/explorer.exe boot. That's
    exactly what left multiple hung createprefix trees running
    indefinitely (one over 30 minutes, still burning ~30% CPU) while
    diagnosing a broken Proton-Experimental build live.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as e:
        logger.warning("[prefix_init] failed to kill umu process group: %s", e)


async def _run_umu(
    plan: ProtonLaunchPlan, env: dict[str, str], *umu_args: str,
) -> None:
    """Spawn ``<python> <umu-run> <args>`` and wait (output discarded).

    Runs in its own process group and is bounded by
    ``_UMU_STEP_TIMEOUT_SECONDS`` — a hung Proton/Wine boot is
    force-killed, process tree and all, instead of orphaned to run
    forever.
    """
    argv = [str(plan.python_bin), str(plan.umu_wrapper), *umu_args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.warning("[prefix_init] umu %s spawn failed: %s", umu_args, e)
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=_UMU_STEP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "[prefix_init] umu %s exceeded %ds — killing process group",
            umu_args, int(_UMU_STEP_TIMEOUT_SECONDS),
        )
        _kill_process_group(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
