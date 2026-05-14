"""auth.edge_browser.env — Session env detection for Edge subprocess.

Extracted from edge_browser.py on 2026-04-18 to isolate the
4-stage graphical-session detection pipeline from the browser
launch concerns. Decky's backend often runs as a service without
the real gaming-mode display variables, so we scrape them from
gamescope-environment files and /proc/<PID>/environ of running
Steam processes.

The ``clean_env`` entry point returns an environment dict suitable
for subprocess.Popen when spawning Edge. It strips PluginLoader's
LD_LIBRARY_PATH / LD_PRELOAD pollution, fills in session env from
4-stage discovery, and seeds Steam window env defaults so gaming
mode can surface the spawned window.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "GAMESCOPE_WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "DESKTOP_SESSION",
    "GTK_IM_MODULE",
    "QT_IM_MODULE",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XMODIFIERS",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
)


def _seed_from_own_env(result: dict[str, str]) -> None:
    """Step 1: seed result from the calling process's own env."""
    for key in _SESSION_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            result[key] = value


def _read_gamescope_env_file(
    runtime_dir: str, result: dict[str, str],
) -> None:
    """Step 2: fill missing keys from gamescope-environment file.

    gamescope-session drops this file on startup with the real
    display variables. Missing keys are added to ``result`` in place.
    """
    gamescope_env = Path(runtime_dir) / "gamescope-environment"
    if not gamescope_env.exists():
        return
    try:
        with gamescope_env.open(
            encoding="utf-8", errors="replace",
        ) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if (
                    key in _SESSION_ENV_KEYS
                    and key not in result
                    and value
                ):
                    result[key] = value
    except OSError:
        # File vanished between exists() and open() —
        # caller will fall through to the PID scan.
        pass


def _parse_proc_environ(
    pid: str, result: dict[str, str],
) -> bool:
    """Parse /proc/<pid>/environ and update ``result`` in place.

    Returns True if the scan found a usable DISPLAY/WAYLAND_DISPLAY,
    signalling the caller it can stop scanning further PIDs.
    """
    try:
        with Path(f"/proc/{pid}/environ").open("rb") as f:
            env_bytes = f.read()
    except (PermissionError, FileNotFoundError, OSError):
        return False
    for entry in env_bytes.split(b"\x00"):
        decoded = entry.decode("utf-8", errors="replace")
        if "=" not in decoded:
            continue
        key, value = decoded.split("=", 1)
        if (
            key in _SESSION_ENV_KEYS
            and key not in result
            and value
        ):
            result[key] = value
    return bool(
        result.get("DISPLAY") or result.get("WAYLAND_DISPLAY"),
    )


def _scan_steam_process_env(
    uid: int, result: dict[str, str],
) -> None:
    """Step 3: scan Steam/gamescope processes' /proc/PID/environ.

    Stops as soon as a PID yields DISPLAY or WAYLAND_DISPLAY.
    """
    try:
        for proc_name in (
            "steam", "gamescope-session", "gamescope",
        ):
            pids = subprocess.run(
                ["pgrep", "-u", str(uid), "-x", proc_name],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,  # pgrep rc=1 on "no match" is expected
            ).stdout.strip().split("\n")
            for raw_pid in pids:
                pid = raw_pid.strip()
                if not pid:
                    continue
                if _parse_proc_environ(pid, result):
                    logger.info(
                        "[Edge] Session env detected from "
                        "PID %s (%s): DISPLAY=%s "
                        "WAYLAND_DISPLAY=%s",
                        pid, proc_name,
                        result.get("DISPLAY"),
                        result.get("WAYLAND_DISPLAY"),
                    )
                    return
    except Exception as e:
        # pgrep missing, scheduling glitch — not fatal, caller
        # falls through to hardcoded fallbacks.
        logger.debug(
            "[Edge] Session env detection error: %s", e,
        )


def _apply_fallbacks(
    uid: int, home: str, runtime_dir: str, result: dict[str, str],
) -> None:
    """Step 4: fill remaining gaps with hardcoded fallbacks.

    Handles gamescope sessions that don't expose env through any
    of the previous discovery mechanisms.
    """
    if (
        not result.get("DISPLAY")
        and not result.get("WAYLAND_DISPLAY")
    ):
        result["DISPLAY"] = ":0"
    if not result.get("XDG_RUNTIME_DIR"):
        result["XDG_RUNTIME_DIR"] = runtime_dir
    if (
        "DBUS_SESSION_BUS_ADDRESS" not in result
        and Path(f"{runtime_dir}/bus").exists()
    ):
        result["DBUS_SESSION_BUS_ADDRESS"] = (
            f"unix:path={runtime_dir}/bus"
        )
    if "XAUTHORITY" not in result:
        xauth_files = [
            str(p) for p in Path(runtime_dir).glob("xauth_*")
        ]
        if xauth_files:
            result["XAUTHORITY"] = xauth_files[0]
        elif (Path(home) / ".Xauthority").exists():
            result["XAUTHORITY"] = str(Path(home) / ".Xauthority")
    if (
        not result.get("WAYLAND_DISPLAY")
        and result.get("GAMESCOPE_WAYLAND_DISPLAY")
        and result.get("XDG_RUNTIME_DIR")
    ):
        gamescope_socket = (
            Path(result["XDG_RUNTIME_DIR"])
            / result["GAMESCOPE_WAYLAND_DISPLAY"]
        )
        if gamescope_socket.exists():
            result["WAYLAND_DISPLAY"] = (
                result["GAMESCOPE_WAYLAND_DISPLAY"]
            )
    if (
        result.get("GTK_IM_MODULE") == "Steam"
        and not result.get("XMODIFIERS")
    ):
        result["XMODIFIERS"] = "@im=Steam"


def _detect_session_env(uid: int, home: str) -> dict[str, str]:
    """Detect the active graphical session env for Steam / gamescope.

    Decky's backend often runs as a service without the real
    gaming-mode display variables. Four-stage discovery:

        1. Seed from our own env
        2. Read gamescope-environment file
        3. Scan running Steam/gamescope /proc/PID/environ
        4. Apply hardcoded fallbacks for missing keys
    """
    result: dict[str, str] = {}
    runtime_dir = f"/run/user/{uid}"

    _seed_from_own_env(result)
    _read_gamescope_env_file(runtime_dir, result)
    _scan_steam_process_env(uid, result)
    _apply_fallbacks(uid, home, runtime_dir, result)
    return result


def clean_env() -> dict:
    """Return a clean environment for launching the auth browser/flatpak.

    - Strips ``LD_LIBRARY_PATH`` / ``LD_PRELOAD``.
    - Detects the real Steam/gamescope session env when Decky lacks it.
    - Seeds Steam window env defaults so gaming mode can surface the window.
    - Clears ``GTK_MODULES`` to suppress canberra-gtk-module warnings.
    """
    home = str(Path.home())
    uid = Path(home).stat().st_uid
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")
    }
    env.update(_detect_session_env(uid, home))
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("SteamGameId", "0")
    env.setdefault("STEAM_COMPAT_APP_ID", "0")
    env.setdefault("SteamAppId", "0")
    env["GTK_MODULES"] = ""
    return env
