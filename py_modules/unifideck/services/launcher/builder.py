"""Steam launch-config builder — produce a standalone ``shortcuts.vdf``.

OP-20f | py_modules/unifideck/services/launcher/builder.py

For some launch scenarios we need to produce a standalone shortcut
file (e.g. for one-off game launches that aren't registered in the
main shortcuts.vdf). ``build_standalone`` produces such a file.

``_pick_first_shortcuts_vdf`` selects the canonical shortcuts.vdf
from the candidate Steam paths.
"""

from __future__ import annotations
from .service import LauncherService


def _pick_first_shortcuts_vdf(userdata_root):
    """Pick first shortcuts VDF."""
    from pathlib import Path as _Path

    root = _Path(userdata_root)
    if not root.is_dir():
        return None
    for user_dir in root.iterdir():
        candidate = user_dir / "config" / "shortcuts.vdf"
        if candidate.is_file():
            return candidate
    return None


def build_standalone() -> LauncherService:
    """Build standalone."""
    from pathlib import Path as _Path
    from ...auth.edge_browser import EdgeBrowser
    from ...event_bus import EventBus
    from ..cloud_save import CloudSaveService
    from ..proton_service import ProtonService
    from ..shortcut import ShortcutService

    bus = EventBus()
    data_dir = _Path("~/.local/share/unifideck").expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    userdata_root = _Path("~/.steam/root/userdata").expanduser()
    shortcuts_file = _pick_first_shortcuts_vdf(userdata_root)
    games_map_path = data_dir / "games.map"
    shortcut_svc = ShortcutService(
        bus=bus,
        shortcuts_path=str(shortcuts_file) if shortcuts_file else "",
        games_map_path=str(games_map_path),
    )
    proton_svc = ProtonService(
        bus=bus,
        config_vdf_path=str(userdata_root / "config" / "config.vdf"),
    )
    cloud_svc = CloudSaveService(
        bus=bus,
        local_save_root=str(data_dir / "saves"),
        cloud_root=None,
    )
    edge_browser = EdgeBrowser(
        cdp_port=9222,
        locale_fn=lambda: "en-US",
    )
    return LauncherService(
        bus=bus,
        shortcut_svc=shortcut_svc,
        proton_svc=proton_svc,
        cloud_svc=cloud_svc,
        edge_browser=edge_browser,
    )
