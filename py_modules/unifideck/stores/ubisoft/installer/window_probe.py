"""stores/ubisoft/installer/window_probe.py — UPC window-visibility probe.

Split out of ``manual_ui.py`` to keep that module focused on the install-driver
state machine. ``upc_window_visible`` answers "does UPC currently have a visible
(foreground) window?" — the foreground-vs-tray signal the manual driver uses to
tell an active install from a backgrounded/finished one.
"""
from __future__ import annotations

from pathlib import Path


def upc_window_visible(env: dict[str, str]) -> bool | None:
    """Whether UPC currently has a visible (foreground) window.

    Uses ``xdotool search --onlyvisible`` (the codebase's window tool,
    see ``launcher/cdp/xcloud_cdp.py``) against the install's own
    ``DISPLAY``. Matching is by window NAME — verified on-device that
    UPC's ``WM_NAME``/``_NET_WM_NAME`` is "Ubisoft Connect" while its
    ``WM_CLASS`` is the generic ``steam_app_0`` (useless to match). The
    ``--name`` arg is a regex, so it also covers "Ubisoft Connect
    Installer" during first-run setup. ``--onlyvisible`` filters to
    mapped (``IsViewable``) windows, so a minimized/tray'd UPC returns
    no match — which is exactly the foreground-vs-tray signal.

    Returns ``True``/``False``, or ``None`` when the probe can't run
    (xdotool absent, no DISPLAY, or it errored) so callers can
    distinguish "no window" from "couldn't check" and never act on the
    latter.
    """
    import shutil
    import subprocess
    if shutil.which("xdotool") is None:
        return None
    display = env.get("DISPLAY") or ":0"
    probe_env = {"DISPLAY": display, "HOME": env.get("HOME") or str(Path.home())}
    for key in ("XAUTHORITY", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY"):
        if env.get(key):
            probe_env[key] = env[key]
    # Old builds titled the window "Uplay"; keep it as a fallback.
    searches = (
        ["xdotool", "search", "--onlyvisible", "--name", "Ubisoft Connect"],
        ["xdotool", "search", "--onlyvisible", "--name", "Uplay"],
    )
    ran_ok = False
    for cmd in searches:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env=probe_env,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        ran_ok = True
        if any(line.strip() for line in result.stdout.splitlines()):
            return True
    return False if ran_ok else None
