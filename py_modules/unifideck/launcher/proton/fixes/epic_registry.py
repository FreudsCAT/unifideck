from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)
_UPLAY_ID_RE = re.compile(r"-UplayId=\s*(\d+)")
@dataclass(frozen=True)
class RegistryInjectionResult:
    """Registry injection result."""
    success: bool
    keys_written: int
    reason: str = ""
def _normalize_prefix_root(prefix_path: Path) -> Path:
    """Normalize prefix root."""
    p = prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p
def _select_active_wineprefix(prefix_root: Path) -> Path:
    """Select active wineprefix."""
    pfx_path = prefix_root / "pfx"
    with contextlib.suppress(OSError):
        if (
            pfx_path.is_symlink()
            and pfx_path.resolve() == prefix_root.resolve()
        ):
            return prefix_root
    if (pfx_path / "system.reg").is_file():
        return pfx_path
    if (prefix_root / "system.reg").is_file():
        return prefix_root
    return pfx_path
def _linux_to_wine_path(linux_path: str) -> str:
    """Linux to WINE path."""
    wine_path = "Z:" + linux_path.replace("/", "\\")
    if not wine_path.endswith("\\"):
        wine_path += "\\"
    return wine_path

def _load_installed_json(
    legendary_config: Path,
    game_id: str,
) -> dict[str, Any] | None:

    """Load installed JSON."""
    installed_json = legendary_config / "installed.json"
    if not installed_json.is_file():
        logger.error(
            "[epic_registry] installed.json not found at %s",
            installed_json,
        )
        return None
    try:
        with installed_json.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("[epic_registry] failed to read installed.json")
        return None
    app = data.get(game_id)
    if not app:
        logger.error(
            "[epic_registry] game %s not in "
            "installed.json", game_id,
        )
        return None
    return cast("dict[Any, Any] | None", app)
def _find_wine_binary() -> Path | None:
    """Find WINE binary."""
    proton_path = os.environ.get("PROTONPATH")
    if not proton_path:
        return None
    wine_bin = (
        Path(proton_path) / "files" / "bin" / "wine"
    )
    return wine_bin if wine_bin.is_file() else None

def _build_reg_commands(
    wine_bin: Path,
    game_id: str,
    wine_install_path: str,
    uplay_id: str | None,
) -> list[list[str]]:

    """Build reg commands."""
    commands: list[list[str]] = [
        [
            str(wine_bin), "reg", "add",
            "HKEY_LOCAL_MACHINE\\Software\\Epic Games\\EpicGamesLauncher",
            "/v", "AppDataPath", "/t", "REG_SZ",
            "/d", "C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data\\",
            "/f",
        ],
        [
            str(wine_bin), "reg", "add",
            (
                "HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Epic Games"
                "\\EpicGamesLauncher\\Manifests\\" + game_id
            ),
            "/v", "InstallLocation", "/t", "REG_SZ",
            "/d", wine_install_path, "/f",
        ],
        [
            str(wine_bin), "reg", "add",
            (
                "HKEY_CURRENT_USER\\Software\\Epic Games"
                "\\EpicGamesLauncher\\Manifests\\" + game_id
            ),
            "/v", "InstallLocation", "/t", "REG_SZ",
            "/d", wine_install_path, "/f",
        ],
    ]
    if uplay_id:
        commands.extend([
            [
                str(wine_bin), "reg", "add",
                (
                    "HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Ubisoft"
                    "\\Launcher\\Installs\\" + uplay_id
                ),
                "/v", "InstallDir", "/t", "REG_SZ",
                "/d", wine_install_path, "/f",
            ],
            [
                str(wine_bin), "reg", "add",
                (
                    "HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Ubisoft"
                    "\\Launcher\\Installs\\" + uplay_id
                ),
                "/v", "Language", "/t", "REG_SZ",
                "/d", "en-US", "/f",
            ],
        ])
    return commands

async def _run_reg_commands(
    commands: list[list[str]],
    env: dict[str, Any],
) -> int:

    """Run reg commands."""
    ok_count = 0
    for cmd in commands:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=30,
                )
            except TimeoutError:
                logger.exception(
                    "[epic_registry] reg add timed out: %s",
                    cmd[3],
                )
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                continue
            if proc.returncode == 0:
                ok_count += 1
            else:
                logger.error(
                    "[epic_registry] reg add failed: %s: %s",
                    cmd[3],
                    stderr.decode(errors="replace").strip(),
                )
        except (OSError, subprocess.SubprocessError):
            logger.exception("[epic_registry] reg add spawn error")
            continue
    return ok_count
async def _kill_wineserver(
    wine_bin: Path, wineprefix: Path,
) -> None:
    """Kill wineserver."""
    wineserver = wine_bin.parent / "wineserver"
    if not wineserver.is_file():
        return
    env = dict(os.environ)
    env["WINEPREFIX"] = str(wineprefix)
    with contextlib.suppress(TimeoutError, OSError,
        subprocess.SubprocessError,):
        proc = await asyncio.create_subprocess_exec(
            str(wineserver), "--kill",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        logger.info(
            "[epic_registry] killed stale wineserver "
            "after setup",
        )
def _resolve_install_paths(
    app: dict[str, Any],
) -> tuple[str, str | None] | None:
    """Resolve install paths."""
    install_path = app.get("install_path")
    if not install_path:
        return None
    wine_install_path = _linux_to_wine_path(install_path)
    launch_params = app.get("launch_parameters", "") or ""
    uplay_match = _UPLAY_ID_RE.search(launch_params)
    uplay_id = uplay_match.group(1) if uplay_match else None
    return wine_install_path, uplay_id

def _error_result(reason: str) -> RegistryInjectionResult:

    """Error result."""
    return RegistryInjectionResult(
        success=False, keys_written=0, reason=reason,
    )
async def setup_registry(
    game_id: str,
    prefix_path: Path,
    legendary_config: Path,
) -> RegistryInjectionResult:
    """Setup registry."""
    prefix_root = _normalize_prefix_root(prefix_path)
    app = _load_installed_json(legendary_config, game_id)
    if app is None:
        return _error_result("installed_json_missing_or_unreadable")
    paths = _resolve_install_paths(app)
    if paths is None:
        logger.error(
            "[epic_registry] no install_path for %s", game_id,
        )
        return _error_result("no_install_path")
    wine_install_path, uplay_id = paths
    wine_bin = _find_wine_binary()
    if wine_bin is None:
        logger.error(
            "[epic_registry] PROTONPATH not set or wine "
            "binary missing",
        )
        return _error_result("wine_binary_not_found")
    active_prefix = _select_active_wineprefix(prefix_root)
    env = dict(os.environ)
    env["WINEPREFIX"] = str(active_prefix)
    commands = _build_reg_commands(
        wine_bin=wine_bin,
        game_id=game_id,
        wine_install_path=wine_install_path,
        uplay_id=uplay_id,
    )
    ok_count = await _run_reg_commands(commands, env)
    await _kill_wineserver(wine_bin, active_prefix)
    total = len(commands)
    all_ok = ok_count == total
    logger.info(
        "[epic_registry] setup for %s (uplay=%s): %d/%d keys",
        game_id, uplay_id, ok_count, total,
    )
    return RegistryInjectionResult(
        success=all_ok,
        keys_written=ok_count,
        reason="" if all_ok else "partial_reg_add_failures",
    )
