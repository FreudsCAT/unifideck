"""Standalone ``LauncherService`` factory for the dispatcher CLI.

OP-20f | py_modules/unifideck/services/launcher/builder.py

When the dispatcher CLI is invoked outside Decky Loader (e.g.
from a Steam shortcut's ``Exec=`` line), there's no
``ServiceBootstrap`` to wire up the launcher's dependencies.
``build_standalone`` is the CLI-only factory that constructs the
minimum set of collaborators directly so the launch path can
still run.

The standalone build differs from the in-plugin one:

* a fresh ``EventBus`` (no subscribers — emissions are
  fire-and-forget);
* no ``StoreRegistry`` (the dispatcher receives the store id
  via the launch context);
* no ``LaunchHistoryService`` (the circuit breaker is bypassed
  in CLI mode because the CLI is itself the manual recovery
  path);
* ``cloud_root=None`` (cloud saves disabled — the user can
  re-run with a full plugin context to sync).

``_pick_first_shortcuts_vdf`` picks the first valid
``shortcuts.vdf`` under ``~/.steam/root/userdata`` — Steam
creates one per logged-in account, the CLI just uses whichever
it finds first.
"""

from __future__ import annotations

from .service import LauncherService


def _pick_first_shortcuts_vdf(userdata_root):
    """Return the first ``shortcuts.vdf`` found under userdata_root.

    Walks ``<userdata_root>/<account>/config/shortcuts.vdf`` for
    each account directory and returns the first file that
    actually exists. Useful when the Steam Deck has a single
    Steam account — multiple accounts are unusual on the device.

    Args:
        userdata_root: path to Steam's ``userdata`` directory.

    Returns:
        ``Path`` of the first valid shortcuts.vdf, or ``None``
        if none was found (fresh Steam, never used).
    """
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
    """Construct a CLI-mode ``LauncherService`` with hard-coded paths.

    Builds the minimum service graph needed to run a launch
    outside Decky Loader:

    * data directory at ``~/.local/share/unifideck/``
      (created on the fly);
    * Steam userdata at ``~/.steam/root/userdata``;
    * games map at ``~/.local/share/unifideck/games.map``;
    * Edge browser bound to CDP port 9222 (the default Edge
      remote-debugging port).

    Returns:
        A ready-to-use ``LauncherService`` instance. The caller
        is responsible for calling ``start`` and ``launch``.
    """
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
