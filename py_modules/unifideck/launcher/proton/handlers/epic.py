from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

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
    candidates = [plan.prefix_path]
    active = os.environ.get("ACTIVE_WINEPREFIX")
    if active:
        candidates.append(Path(active))
    return candidates


def _remove_epic_launcher_stubs(drive_c: Path) -> None:
    """Delete known ``EpicGamesLauncher.exe`` stub paths under a
    Wine prefix's ``drive_c``. The stubs ship as part of certain
    template prefixes and confuse ``legendary`` if left in place.
    Failures are logged but never raised — a stale stub is a
    convenience issue, not a launch blocker.
    """
    if not drive_c.is_dir():
        return
    for rel in _EPIC_LAUNCHER_STUBS:
        target = drive_c / rel
        if not target.is_file():
            continue
        try:
            target.unlink()
            logger.info(
                "[launcher.proton.epic] removed stub: %s",
                target,
            )
        except OSError as e:
            logger.debug(
                "[launcher.proton.epic] could not remove stub %s: %s",
                target, e,
            )


def _clean_epic_registry(reg: Path) -> None:
    """Strip Epic-launcher COM registration from a Wine ``.reg`` file.

    Operates on ``user.reg``/``system.reg``. Reads the file, runs
    it through :func:`_strip_registry_section`, writes back only
    if content changed. Any I/O or parse failure is logged at
    debug level — the registry hygiene is best-effort.
    """
    if not reg.is_file():
        return
    try:
        content = reg.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[launcher.proton.epic] reg read failed %s: %s", reg, e)
        return
    if _EPIC_REGISTRY_KEY not in content:
        return
    new_content = _strip_registry_section(content, _EPIC_REGISTRY_KEY)
    if new_content == content:
        return
    try:
        reg.write_text(new_content, encoding="utf-8")
        logger.info(
            "[launcher.proton.epic] cleaned %s from %s",
            _EPIC_REGISTRY_KEY, reg.name,
        )
    except OSError as e:
        logger.debug("[launcher.proton.epic] reg write failed %s: %s", reg, e)


def _lazy_cleanup_epic_artifacts(plan: ProtonLaunchPlan) -> None:
    """Remove Epic-launcher leftovers before ``legendary`` runs.

    Two passes:
      1. Delete ``EpicGamesLauncher.exe`` stubs from every
         candidate prefix's ``drive_c``.
      2. Strip ``com.epicgames.launcher`` registry blocks from
         every prefix's ``user.reg`` / ``system.reg``.

    All failures are swallowed (logged at debug level) — we never
    want preflight hygiene to block the launch itself.
    """
    prefix_candidates = _collect_prefix_candidates(plan)
    for prefix in prefix_candidates:
        _remove_epic_launcher_stubs(prefix / "drive_c")
    for prefix in prefix_candidates:
        for reg_name in ("user.reg", "system.reg"):
            _clean_epic_registry(prefix / reg_name)

def _strip_registry_section(content: str, section_key: str) -> str:

    """Strip registry section."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    header_pat = re.compile(
        r"^\[.*" + re.escape(section_key) + r".*\]",
    )
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
def _resolve_exe_override(plan: ProtonLaunchPlan) -> Path | None:
    """Resolve exe override."""
    from unifideck.launcher.proton.fixes.game_fixes import get_exe_override
    rel = get_exe_override(plan.context.game_id)
    if not rel:
        return None
    installed = Path(
        "~/.config/legendary/installed.json",
    ).expanduser()
    if not installed.is_file():
        return None
    try:
        with installed.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    install_path = (
        data.get(plan.context.game_id, {}).get("install_path")
    )
    if not install_path:
        return None
    full = Path(install_path) / rel
    return full if full.is_file() else None

async def _run_epic_prerequisites(plan: ProtonLaunchPlan) -> None:
    """Run epic prerequisites."""
    from unifideck.launcher.proton.fixes.epic_prerequisites import (
        apply_epic_prerequisites,
    )
    try:
        await apply_epic_prerequisites(plan)
    except Exception:
        logger.exception(
            "[launcher.proton.epic] prerequisites step crashed "
            "(non-fatal)",
        )


async def epic_launch(plan: ProtonLaunchPlan) -> int:

    """Epic launch."""
    logger.info(
    "[launcher.proton.epic] launching %s", plan.context.game_key,
   )
    _lazy_cleanup_epic_artifacts(plan)
    await _run_epic_prerequisites(plan)
    env = dict(plan.env)
    env["STORE"] = "none"
    env.pop("LEGENDARY_WRAPPER_EXE", None)
    exe_override = _resolve_exe_override(plan)
    legendary_bin = os.environ.get("LEGENDARY_BIN", "legendary")
    argv: list[str] = list(plan.state.wrappers)
    argv.extend([
    legendary_bin,
    "launch",
    plan.context.game_id,
    "--no-wine",
    "--skip-version-check",
    "--wrapper",
    f"{plan.python_bin} {plan.umu_wrapper}",
    "--language",
    os.environ.get("EPIC_LANG", "en"),
    ])
    if exe_override:
        argv.extend(["--override-exe", str(exe_override)])
        logger.info(
            "[launcher.proton.epic] using EXE override: %s",
            exe_override,
        )
    if plan.state.game_args:
        argv.append("--")
        argv.extend(plan.state.game_args)
    rc = await run_umu_with_retry(
        argv, env=env, on_start=plan.on_process_start,
    )
    plan.state.game_exit_code = rc
    if rc == 0:
        return 0
    if rc in {2, 74}:
        raise UmuRuntimeError(
            f"umu-run failed with unrecoverable code {rc}",
            context={"subprocess_rc": rc, "store": "epic"},
        )
    raise GameFailedError(
        f"Epic game exited with code {rc}",
        subprocess_rc=rc,
        context={
            "store": "epic",
            "game_id": plan.context.game_id,
        },
    )
