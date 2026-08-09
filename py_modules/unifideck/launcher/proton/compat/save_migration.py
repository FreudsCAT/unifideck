"""compat/save_migration.py — bring a game's saves forward into a prefix.

Split out of ``compat/prefix_init.py``, which owns the prefix *lifecycle*
(reset on a Proton change, createprefix, the umu subprocess ladder). This
module owns the orthogonal concern of not losing user data across those
events, and has two sources for it:

1. **``.save_backup``** — ``prefix_init._reset_prefix`` copies
   ``drive_c/users`` aside before wiping a prefix. Nothing used to put it
   back, so a Proton-family change silently lost saves.
2. **The legacy shared umu prefix** — pre-0.6 launches didn't set
   ``WINEPREFIX``, so games ran in umu's default prefix
   (``~/Games/umu/umu-0``). After upgrading, the new per-game prefix is empty
   and the saves look lost.

Every copy is a **non-destructive, mtime-guarded merge**: a file is written
only when the destination is missing or older, so a save written after a reset
is never clobbered by a stale backup and a repeat run is harmless. Everything
here is best-effort — a failure logs and the launch proceeds.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.launcher.proton.infrastructure.prefix_layout import (
    resolve_registry_prefix,
)

if TYPE_CHECKING:
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

# Written into a per-game prefix once the one-time legacy-save migration
# has run, so we don't rescan the legacy umu prefixes on every launch.
_LEGACY_MIGRATED_MARKER = ".unifideck_legacy_migrated"
# Shared umu prefixes used before 0.6 set a per-game WINEPREFIX. Games
# launched then wrote their saves into umu's default prefix; we pull
# those forward on the first per-game prefix init.
_LEGACY_UMU_BASE = "~/Games/umu"
_LEGACY_UMU_SHARED = ("umu-0", "umu-default")


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


async def restore_or_migrate_saves(
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
