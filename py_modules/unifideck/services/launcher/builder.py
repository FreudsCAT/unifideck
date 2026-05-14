"""services/launcher/builder.py — Standalone CLI factory.

Factory used exclusively by ``launcher/dispatcher.py`` when the
Python process spawned by ``bin/unifideck-launcher`` needs a
``LauncherService`` but can't access the live plugin's
``ServiceContainer`` (plugin runs in a separate Decky Loader
interpreter).

Minimal service graph: EventBus + ShortcutService +
ProtonService + CloudSaveService + EdgeBrowser + LauncherService.
Bypasses ``ConfigManager`` — the dispatcher is short-lived and
doesn't need feature flags or UI locale; 50 ms boot cost saved.
"""
from __future__ import annotations

import glob
import os

from .service import LauncherService


def _pick_first_shortcuts_vdf(userdata_root: str) -> str | None:
    """Find a ``shortcuts.vdf`` under Steam's userdata dir.

    Scans ``~/.steam/root/userdata/*/config/shortcuts.vdf`` and
    returns the first match — same heuristic the plugin uses at
    boot so both processes read the same file. Returns None if
    no Steam profiles exist (fresh install, missing SteamOS).
    """
    pattern = os.path.join(userdata_root, "*", "config", "shortcuts.vdf")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    return None


def build_standalone() -> LauncherService:
    """Build a fully-wired LauncherService for the CLI dispatcher.

    Paths match what ``main.py`` configures but are hardcoded
    here to avoid loading ConfigManager. Does not explicitly
    raise — underlying ctors may raise OSError on some
    filesystem errors, which the dispatcher maps to
    ``ExitCode.DEPENDENCY_MISSING``. Cloud sync is disabled
    (``cloud_root=None``) in the standalone path: the plugin's
    ServiceBootstrap wires the real root from config; the CLI
    only needs local saves.
    """
    from unifideck.auth.edge_browser import EdgeBrowser
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.services.cloud_save.service import CloudSaveService
    from unifideck.services.proton_service.service import ProtonService
    from unifideck.services.shortcut.service import ShortcutService

    bus = EventBus()

    # Standalone paths
    steam_root = os.path.expanduser("~/.steam/root")
    userdata_root = os.path.join(steam_root, "userdata")
    plugin_dir = os.path.expanduser("~/homebrew/plugins/unifideck")
    local_saves_root = os.path.expanduser("~/.local/share/unifideck/saves")

    shortcuts_vdf = _pick_first_shortcuts_vdf(userdata_root)

    shortcut_svc = ShortcutService(
        bus=bus,
        plugin_dir=plugin_dir,
        shortcuts_vdf_path=shortcuts_vdf or "",
    )

    proton_svc = ProtonService()

    cloud_svc = CloudSaveService(
        bus=bus,
        local_save_root=local_saves_root,
        cloud_root=None, # Disabled in CLI
        config=None,
    )

    edge_browser = EdgeBrowser()

    return LauncherService(
        bus=bus,
        shortcut_svc=shortcut_svc,
        proton_svc=proton_svc,
        cloud_svc=cloud_svc,
        edge_browser=edge_browser,
    )

