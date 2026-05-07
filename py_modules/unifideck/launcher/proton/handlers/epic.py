from __future__ import annotations
import json
import logging
import os
import re
from pathlib import Path
from ...types.errors import GameFailedError, UmuRuntimeError
from ..infrastructure.core import ProtonLaunchPlan
from ..infrastructure.umu_runtime import run_umu_with_retry
logger = logging.getLogger(__name__)

def _lazy_cleanup_ubisoft_artifacts(plan: ProtonLaunchPlan) -> None:

    """Lazy cleanup UBISOFT artifacts."""
    drive_cs = [plan.prefix_path / "drive_c"]
    active = os.environ.get("ACTIVE_WINEPREFIX")
    if active:
        drive_cs.append(Path(active) / "drive_c")
    targets_per_drive = [
        "windows/command/EpicGamesLauncher.exe",
        (
            "Program Files (x86)/Epic Games/Launcher/"
            "Portal/Binaries/Win32/EpicGamesLauncher.exe"
        ),
    ]
    for drive_c in drive_cs:
        if not drive_c.is_dir():
            continue
        for rel in targets_per_drive:
            target = drive_c / rel
            if target.is_file():
                try:
                    target.unlink()
                    logger.info(
                        "[launcher.proton.epic] removed stub: %s",
                        target,
                    )
                except OSError:
                    pass
    prefix_candidates = [plan.prefix_path]
    if active:
        prefix_candidates.append(Path(active))
    for prefix in prefix_candidates:
        for reg_name in ("user.reg", "system.reg"):
            reg = prefix / reg_name
            if not reg.is_file():
                continue
            try:
                content = reg.read_text(
                    encoding="utf-8", errors="replace",
                )
            except OSError:
                continue
            if "com.epicgames.launcher" not in content:
                continue
            new_content = _strip_registry_section(
                content, "com.epicgames.launcher",
            )
            if new_content != content:
                try:
                    reg.write_text(
                        new_content, encoding="utf-8",
                    )
                    logger.info(
                        "[launcher.proton.epic] cleaned %s "
                        "from %s",
                        "com.epicgames.launcher", reg.name,
                    )
                except OSError:
                    pass

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
    from ..fixes.game_fixes import get_exe_override
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

async def epic_launch(plan: ProtonLaunchPlan) -> int:

    """Epic launch."""
    logger.info(
    "[launcher.proton.epic] launching %s", plan.context.game_key,
   )
    _lazy_cleanup_ubisoft_artifacts(plan)
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