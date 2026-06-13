from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from unifideck.launcher.types.errors import (
    DependencyMissingError,
    ProtonUnavailableError,
)

from . import ge_installer

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
# Steam's internal compat-tool ids → the on-disk install directory
# name. These differ for the official Protons: the id is
# ``proton_experimental`` but the folder is ``Proton - Experimental``.
# Without this mapping the Experimental fallback — and any user who
# force-selects an official Proton in Steam — never resolves to a path.
OFFICIAL_TOOL_DIRS: dict[str, str] = {
    "proton_experimental": "Proton - Experimental",
    "proton_hotfix": "Proton Hotfix",
    "proton_10": "Proton 10.0",
    "proton_9": "Proton 9.0 (Beta)",
    "proton_8": "Proton 8.0",
    "proton_7": "Proton 7.0",
}
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
    # Official Proton tools live in steamapps/common under a display-name
    # dir that differs from the tool id (see OFFICIAL_TOOL_DIRS). Try the
    # id verbatim first, then the mapped directory name.
    dir_names = [tool_id]
    aliased = OFFICIAL_TOOL_DIRS.get(tool_id)
    if aliased:
        dir_names.append(aliased)
    for lib in STEAM_LIBRARY_ROOTS:
        for name in dir_names:
            candidate = Path(lib).expanduser() / name / "proton"
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
      4. The latest GE-Proton released online (downloaded/installed on
         demand), falling back to Proton Experimental when offline.
    """
    tried: list[str] = []
    saved_tool = get_saved_proton_tool(store_game_id) if store_game_id else None
    if saved_tool:
        path = _resolve_logged("saved", saved_tool, tried)
        if path:
            return path, saved_tool
    steam_tool = (
        get_steam_compat_tool_override(steam_app_id) if steam_app_id else None
    )
    if steam_tool:
        path = _resolve_logged("steam", steam_tool, tried)
        if path:
            return path, steam_tool
    unifideck_tool = get_unifideck_proton_tool()
    if unifideck_tool:
        path = _resolve_logged("unifideck", unifideck_tool, tried)
        if path:
            return path, unifideck_tool
    return _default_latest_ge(tried)


def _resolve_logged(source: str, tool: str, tried: list[str]) -> Path | None:
    """Record the attempt, resolve the tool to a path, log on success."""
    tried.append(f"{source}:{tool}")
    path = resolve_proton_path(tool)
    if path:
        logger.info("[launcher.proton] selected via %s tool: %s", source, tool)
    return path


class _GeDownloadAnnouncer:
    """A ``progress_cb`` that toasts once when a real download starts.

    ``ge_installer`` invokes this per byte chunk, but only when an
    actual download happens (it returns early when GE is already
    installed). We fire a single "downloading Proton" toast on the
    first chunk and record ``fired`` so the caller knows whether to
    also toast "ready". Best-effort — a toast failure never breaks
    Proton selection.
    """

    def __init__(self) -> None:
        self.fired = False

    def __call__(self, _done: int, _total: int) -> None:
        if self.fired:
            return
        self.fired = True
        try:
            from unifideck.launcher.frontend_bridge import launcher_toast
            launcher_toast(
                "toasts.launcher.downloadingProton",
                i18n_title_key="toasts.launcher.installingProton",
            )
        except Exception:
            logger.debug("[launcher.proton] GE download toast failed", exc_info=True)


def _announce_ge_ready(tag: str) -> None:
    """Toast that the just-downloaded GE-Proton is ready (best-effort)."""
    try:
        from unifideck.launcher.frontend_bridge import launcher_toast
        launcher_toast(
            "toasts.launcher.protonReadyBody",
            i18n_title_key="toasts.launcher.protonReadyTitle",
            i18n_params={"version": tag},
        )
    except Exception:
        logger.debug("[launcher.proton] GE ready toast failed", exc_info=True)


def _default_latest_ge(tried: list[str]) -> tuple[Path, str]:
    """Default tier: latest GE-Proton online, else Proton Experimental.

    1. Fast path — if the background installer recorded a latest tag
       (``proton_ge_latest.json``) and it is validly installed, use it
       without touching the network.
    2. Safety net — fetch the newest GE-Proton tag and download/install
       it on demand (bounded; offline returns ``None`` quickly).
    3. Fallback — Proton Experimental (the only fallback by design;
       older local GE versions stay user-selectable via Force Compat).
    """
    cached = ge_installer.read_cached_latest_tag()
    if cached:
        path = ge_installer.installed_ge_proton_path(cached)
        if path:
            tried.append(f"latest-ge-cached:{cached}")
            logger.info(
                "[launcher.proton] selected cached latest GE-Proton: %s", cached,
            )
            return path, cached

    # On-demand download at launch time — the background installer
    # hasn't finished (or never ran). This is otherwise silent, leaving
    # the user staring at a frozen-looking launch while a ~hundreds-of-MB
    # Proton downloads, so toast when a real download starts/finishes.
    # ``progress_cb`` fires only during the actual byte stream, so the
    # toast never appears when GE is already installed (no download).
    announcer = _GeDownloadAnnouncer()
    result = ge_installer.ensure_latest_ge(progress_cb=announcer)
    if result:
        path, tag = result
        tried.append(f"latest-ge:{tag}")
        logger.info("[launcher.proton] selected latest GE-Proton: %s", tag)
        if announcer.fired:  # only when a download actually happened
            _announce_ge_ready(tag)
        return path, tag

    tried.append("fallback:proton_experimental")
    experimental = resolve_proton_path("proton_experimental")
    if experimental:
        logger.info(
            "[launcher.proton] GE-Proton unavailable; "
            "falling back to Proton Experimental",
        )
        return experimental, "proton_experimental"

    raise ProtonUnavailableError(
        "No usable Proton compat tool found",
        context={"tried": tried},
    )
