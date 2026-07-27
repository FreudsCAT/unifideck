"""compat/epic_cleanup.py — pre-launch Epic prefix hygiene.

Removes Epic-launcher leftovers that confuse ``legendary`` before it
runs: stray ``EpicGamesLauncher.exe`` stubs (shipped by some template
prefixes) and ``com.epicgames.launcher`` COM registrations in the
prefix registry. All steps are best-effort — stale leftovers are a
convenience issue, never a launch blocker, so failures log at debug.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.prefix_layout import (
    resolve_drive_c,
    resolve_registry_prefix,
)

logger = logging.getLogger(__name__)

_EPIC_LAUNCHER_STUBS = (
    "windows/command/EpicGamesLauncher.exe",
    (
        "Program Files (x86)/Epic Games/Launcher/"
        "Portal/Binaries/Win32/EpicGamesLauncher.exe"
    ),
)

_EPIC_REGISTRY_KEY = "com.epicgames.launcher"


def _collect_prefix_candidates(plan: ProtonLaunchPlan) -> list[Path]:
    """Return prefix paths to inspect: the plan's prefix plus any
    ``ACTIVE_WINEPREFIX`` env override (used during cross-store
    debugging when the user temporarily aims at a different
    prefix). The list always contains at least one entry.
    """
    import os
    candidates = [plan.prefix_path]
    active = os.environ.get("ACTIVE_WINEPREFIX")
    if active:
        candidates.append(Path(active))
    return candidates


def _remove_epic_launcher_stubs(drive_c: Path) -> None:
    """Delete known ``EpicGamesLauncher.exe`` stub paths under a
    Wine prefix's ``drive_c``. The stubs ship as part of certain
    template prefixes and confuse ``legendary`` if left in place.
    """
    if not drive_c.is_dir():
        return
    for rel in _EPIC_LAUNCHER_STUBS:
        target = drive_c / rel
        if not target.is_file():
            continue
        try:
            target.unlink()
            logger.info("[epic_cleanup] removed stub: %s", target)
        except OSError as e:
            logger.debug(
                "[epic_cleanup] could not remove stub %s: %s", target, e,
            )


def _clean_epic_registry(reg: Path) -> None:
    """Strip Epic-launcher COM registration from a Wine ``.reg`` file.

    Operates on ``user.reg``/``system.reg``. Writes back only if the
    content changed. Any I/O or parse failure is logged at debug —
    the registry hygiene is best-effort.
    """
    if not reg.is_file():
        return
    try:
        content = reg.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[epic_cleanup] reg read failed %s: %s", reg, e)
        return
    if _EPIC_REGISTRY_KEY not in content:
        return
    new_content = _strip_registry_section(content, _EPIC_REGISTRY_KEY)
    if new_content == content:
        return
    try:
        reg.write_text(new_content, encoding="utf-8")
        logger.info(
            "[epic_cleanup] cleaned %s from %s", _EPIC_REGISTRY_KEY, reg.name,
        )
    except OSError as e:
        logger.debug("[epic_cleanup] reg write failed %s: %s", reg, e)


def _strip_registry_section(content: str, section_key: str) -> str:
    """Remove every ``[...section_key...]`` block from a Wine .reg."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    header_pat = re.compile(r"^\[.*" + re.escape(section_key) + r".*\]")
    next_section_pat = re.compile(r"^\[")
    for line in lines:
        if (
            skipping
            and next_section_pat.match(line)
            and not header_pat.match(line)
        ):
            skipping = False
            out.append(line)
            continue
        if header_pat.match(line):
            skipping = True
            continue
        if skipping:
            continue
        out.append(line)
    return "".join(out)


def cleanup_epic_artifacts(plan: ProtonLaunchPlan) -> None:
    """Remove Epic-launcher leftovers before ``legendary`` runs.

    Two passes over every candidate prefix: delete the
    ``EpicGamesLauncher.exe`` stubs from ``drive_c``, then strip
    ``com.epicgames.launcher`` registry blocks from ``user.reg`` /
    ``system.reg``. All failures are swallowed (logged at debug) — we
    never want preflight hygiene to block the launch itself.

    umu/Proton nest the real Wine tree under ``<prefix>/pfx/`` rather
    than the prefix root directly — resolve through the same helpers
    ``prefix_init.py`` uses, or this silently inspects paths that never
    exist and never actually cleans anything.

    Rockstar-on-Epic (RDR2/GTA5) is the exception: those games DEPEND on
    the ``EpicGamesLauncher.exe`` stub + the ``com.epicgames.launcher``
    registration to boot the Rockstar launcher, so this hygiene is
    skipped entirely for them (it would delete exactly what they need).
    """
    from unifideck.launcher.proton.fixes.game_fixes import is_rockstar_egs
    if is_rockstar_egs(
        plan.context.game_id, plan.state.umu_id, plan.context.exe_path.name,
    ):
        logger.info(
            "[epic_cleanup] Rockstar-EGS (%s): skipping launcher-stub/"
            "registry cleanup (the game needs them)", plan.state.umu_id,
        )
        return
    for prefix in _collect_prefix_candidates(plan):
        drive_c = resolve_drive_c(prefix)
        if drive_c is not None:
            _remove_epic_launcher_stubs(drive_c)
    for prefix in _collect_prefix_candidates(plan):
        registry_root = resolve_registry_prefix(prefix)
        for reg_name in ("user.reg", "system.reg"):
            _clean_epic_registry(registry_root / reg_name)
