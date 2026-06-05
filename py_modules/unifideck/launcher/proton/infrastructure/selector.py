from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from unifideck.launcher.types.errors import (
    DependencyMissingError,
    ProtonUnavailableError,
)

logger = logging.getLogger(__name__)
PYTHON_CANDIDATES: list[str] = [
    "/usr/bin/python3.13",
    "/usr/bin/python3.12",
    "/usr/bin/python3.11",
    "/usr/bin/python3.10",
    "/usr/bin/python3",
]
ACCEPTED_VERSIONS = {"3.10", "3.11", "3.12", "3.13", "3.14"}
def find_python_3_10_plus() -> Path:
    """Find python 3 10 plus."""
    for candidate in PYTHON_CANDIDATES:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            out = subprocess.check_output(
                [
                    candidate,
                    "-c",
                    'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")',
                ],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        ver = out.decode().strip()
        if ver in ACCEPTED_VERSIONS:
            logger.info("[launcher.proton] python selected: %s (%s)", candidate, ver)
            return path
    raise DependencyMissingError(
        "No Python 3.10+ interpreter found on system",
        context={"tried": PYTHON_CANDIDATES},
    )

STEAM_COMPAT_ROOTS: list[str] = [
    "~/.steam/root/compatibilitytools.d",
    "~/.local/share/Steam/compatibilitytools.d",
]
STEAM_LIBRARY_ROOTS: list[str] = [
    "~/.steam/root/steamapps/common",
    "~/.local/share/Steam/steamapps/common",
]
UNIFIDECK_COMPAT_DIR = "~/.local/share/unifideck/compat-tools"
def resolve_proton_path(tool_id: str) -> Path | None:
    """Resolve PROTON path."""
    if not tool_id:
        return None
    unifideck_path = Path(UNIFIDECK_COMPAT_DIR).expanduser() / tool_id / "proton"
    if unifideck_path.is_file():
        return unifideck_path
    for root in STEAM_COMPAT_ROOTS:
        candidate = Path(root).expanduser() / tool_id / "proton"
        if candidate.is_file():
            return candidate
    for lib in STEAM_LIBRARY_ROOTS:
        candidate = Path(lib).expanduser() / tool_id / "proton"
        if candidate.is_file():
            return candidate
    return None
def get_unifideck_proton_tool() -> str | None:
    """Get unifideck PROTON tool."""
    config_path = Path("~/.local/share/unifideck/config.json").expanduser()
    if not config_path.is_file():
        return None
    try:
        import json
        with config_path.open() as f:
            cfg = json.load(f)
        tool = cfg.get("compat", {}).get("proton_tool", "")
        return tool or None
    except (OSError, ValueError):
        return None
def get_saved_proton_tool(store_game_id: str) -> str | None:
    """Return the per-game Proton tool saved by the frontend.

    When the user sets "Force Compatibility" on a Unifideck
    shortcut, the game-details page saves that tool into
    ``proton_settings.json`` (keyed by ``store:game_id``) and
    clears Force Compatibility from Steam so ``RunGame`` runs
    this launcher natively instead of wrapping it in Proton.
    The launcher then applies the saved tool itself — this is
    the lookup that makes the user's choice authoritative.
    """
    if not store_game_id:
        return None
    settings_path = Path(
        "~/.local/share/unifideck/proton_settings.json",
    ).expanduser()
    if not settings_path.is_file():
        return None
    try:
        import json
        with settings_path.open() as f:
            settings = json.load(f)
        tool = settings.get("games", {}).get(store_game_id, "")
        return tool or None
    except (OSError, ValueError):
        return None
_COMPAT_TOOL_RE = re.compile(
    r'"(?P<app_id>\d+)"\s*\{[^}]*?"name"\s*"(?P<name>[^"]+)"',
    re.S,
)
def get_steam_compat_tool_override(app_id: str) -> str | None:
    """Get steam compat tool override."""
    if not app_id:
        return None
    userdata = Path("~/.steam/root/userdata").expanduser()
    if not userdata.is_dir():
        return None
    for user_dir in userdata.iterdir():
        cfg = user_dir / "config" / "localconfig.vdf"
        if not cfg.is_file():
            continue
        try:
            content = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _COMPAT_TOOL_RE.finditer(content):
            if m.group("app_id") == app_id:
                return m.group("name")
    return None
def _ge_version_key(proton_script: Path) -> tuple[int, ...]:
    """Numeric version tuple from a GE-Proton dir (e.g. (10, 34)).

    MUST be numeric, not lexical: a plain string sort puts
    ``GE-Proton9-26`` *after* ``GE-Proton10-34`` ('9' > '1'), so the
    "newest" fallback would pick an OLD Proton — which crashes recent
    titles (e.g. 2025 Unity games) that need a current Proton.
    """
    nums = re.findall(r"\d+", proton_script.parent.name)
    return tuple(int(n) for n in nums) or (0,)


def find_any_ge_proton() -> Path | None:
    """Find the newest installed GE-Proton (by version, not name)."""
    candidates: list[Path] = []
    for root in STEAM_COMPAT_ROOTS:
        expanded = Path(root).expanduser()
        if not expanded.is_dir():
            continue
        for entry in expanded.iterdir():
            if entry.name.startswith("GE-Proton"):
                proton_script = entry / "proton"
                # Must be EXECUTABLE — a broken/partial extract (e.g. a
                # GE-Proton whose `proton` is 0644) would otherwise be
                # picked as "newest" and die with "Permission denied" on
                # exec. Skip it so we fall back to the newest WORKING one.
                if proton_script.is_file() and os.access(
                    proton_script, os.X_OK,
                ):
                    candidates.append(proton_script)
    if not candidates:
        return None
    candidates.sort(key=_ge_version_key)
    return candidates[-1]

def select_proton_version(
    steam_app_id: str | None = None,
    store_game_id: str | None = None,
) -> tuple[Path, str]:

    """Select PROTON version.

    Priority order:
      1. Per-game tool the frontend saved into
         ``proton_settings.json`` (the user's Force-Compat choice,
         captured + cleared on the game-details page).
      2. A live Steam compat override for ``steam_app_id``.
      3. The Unifideck default from ``config.json``.
      4. Newest installed GE-Proton as a last resort.
    """
    tried: list[str] = []
    if store_game_id:
        saved_tool = get_saved_proton_tool(store_game_id)
        if saved_tool:
            tried.append(f"saved:{saved_tool}")
            path = resolve_proton_path(saved_tool)
            if path:
                logger.info(
                    "[launcher.proton] selected via saved per-game tool: %s",
                    saved_tool,
                )
                return path, saved_tool
    if steam_app_id:
        steam_tool = get_steam_compat_tool_override(steam_app_id)
        if steam_tool:
            tried.append(f"steam:{steam_tool}")
            path = resolve_proton_path(steam_tool)
            if path:
                logger.info(
                    "[launcher.proton] selected via Steam override: %s",
                    steam_tool,
                )
                return path, steam_tool
    unifideck_tool = get_unifideck_proton_tool()
    if unifideck_tool:
        tried.append(f"unifideck:{unifideck_tool}")
        path = resolve_proton_path(unifideck_tool)
        if path:
            logger.info(
                "[launcher.proton] selected via Unifideck default: %s",
                unifideck_tool,
            )
            return path, unifideck_tool
    fallback = find_any_ge_proton()
    if fallback:
        tool_id = fallback.parent.name
        tried.append(f"fallback:{tool_id}")
        logger.info("[launcher.proton] selected via GE-Proton fallback: %s", tool_id)
        return fallback, tool_id
    raise ProtonUnavailableError(
        "No usable Proton compat tool found",
        context={"tried": tried},
    )
