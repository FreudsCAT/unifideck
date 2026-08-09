"""Persistent auth shortcuts for wrapper stores.

py_modules/unifideck/stores/shared/auth_shortcut.py

Wrapper stores sign in by running their vendor client. In Desktop Mode the
client can be spawned directly, but **in Gaming Mode it must come from a
Steam shortcut** — a bare subprocess gets no gamescope session, so its
window never appears. That is why signing in works on the desktop and
silently fails on the deck without one of these.

Generic over the store: everything that differs is in
``AuthShortcutSpec``, so EA App is a spec rather than another module.

Two Steam behaviours drive the shape here:

* **Steam reads ``shortcuts.vdf`` only at startup.** A shortcut written
  this session is absent from Steam's in-memory app store, and ``RunGame``
  on its appid fails with "Game configuration unavailable". The frontend
  handles that with a temporary shortcut; this module just has to return a
  ``launcher_path`` so it can.
* **The appid must be derived, not invented** — ``generate_app_id`` is a
  CRC of launcher plus identity, and the same inputs must always give the
  same appid or the shortcut is orphaned on the next run.

Ubisoft keeps its own richer implementation for now (it also prunes legacy
template shortcuts and integrates with its registry). Migrating it onto
this is a follow-up that wants device testing, since it is a shipped and
working auth path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuthShortcutSpec:
    """Everything store-specific about one wrapper store's auth shortcut."""

    store: str
    #: ``store:id`` written into LaunchOptions, e.g. ``battlenet:bnet-auth``.
    store_game_id: str
    #: Shortcut name in Steam, e.g. ``Battle.net``.
    display_name: str
    #: Env token telling the launcher this run is a sign-in.
    action_env: str
    #: Milliseconds the frontend should wait for Steam to register it.
    launch_wait_ms: int = 3000

    def launch_options(self, launcher_path: str) -> str:
        """LaunchOptions for the shortcut. Must be byte-stable.

        ``validate`` compares against this exactly, so any change here
        orphans existing shortcuts until they are rewritten.
        """
        del launcher_path
        return f"{self.store_game_id} {self.action_env}=auth"


def launcher_path_for(plugin_dir: str | None) -> str:
    """Absolute path to the shortcut launcher binary."""
    base = Path(plugin_dir) if plugin_dir else Path(__file__).resolve().parents[3]
    return str(base / "bin" / "unifideck-launcher")


def _entry_matches(entry: Any, spec: AuthShortcutSpec) -> bool:
    if not isinstance(entry, dict):
        return False
    options = str(entry.get("LaunchOptions") or "")
    return spec.store_game_id in options


def find_in_vdf(shortcuts: dict[str, Any], spec: AuthShortcutSpec) -> int | None:
    """Existing appid for this auth shortcut, or None."""
    for entry in shortcuts.values():
        if _entry_matches(entry, spec):
            appid = entry.get("appid") if isinstance(entry, dict) else None
            if isinstance(appid, int):
                return appid
    return None


def _build_entry(
    spec: AuthShortcutSpec, launcher_path: str, appid: int,
) -> dict[str, Any]:
    return {
        "appid": appid,
        "AppName": spec.display_name,
        "Exe": f'"{launcher_path}"',
        "StartDir": f'"{Path(launcher_path).parent}"',
        "LaunchOptions": spec.launch_options(launcher_path),
        # Hidden: it is an infrastructure tile, not a game the user browses.
        "IsHidden": 1,
        "AllowDesktopConfig": 1,
        "OpenVR": 0,
        "tags": {"0": spec.display_name},
    }


async def ensure_auth_shortcut(
    shortcut_service: Any,
    spec: AuthShortcutSpec,
    plugin_dir: str | None,
) -> int | None:
    """Create or repair the persistent auth shortcut. Returns its unsigned appid.

    Never raises: a missing shortcut service or an unwritable VDF degrades
    to ``None``, and the frontend falls back to a temporary shortcut.
    """
    if shortcut_service is None:
        logger.debug("[%s] no shortcut service — cannot create auth shortcut", spec.store)
        return None

    launcher_path = launcher_path_for(plugin_dir)
    try:
        appid = shortcut_service.generate_app_id(launcher_path, spec.display_name)
        unsigned = appid if appid >= 0 else appid + 2**32

        data = await shortcut_service.read_shortcuts()
        shortcuts = data.get("shortcuts", {})

        existing = find_in_vdf(shortcuts, spec)
        if existing is not None:
            logger.info("[%s] auth shortcut already in VDF (appid=%s)", spec.store, existing)
            return existing if existing >= 0 else existing + 2**32

        indices = [int(k) for k in shortcuts if str(k).isdigit()]
        shortcuts[str(max(indices, default=-1) + 1)] = _build_entry(
            spec, launcher_path, appid,
        )
        data["shortcuts"] = shortcuts
        await shortcut_service.write_shortcuts(data)
        logger.info("[%s] created auth shortcut in VDF (appid=%d)", spec.store, unsigned)
    except Exception:
        logger.exception("[%s] auth shortcut creation failed", spec.store)
        return None
    return int(unsigned)


async def build_context(
    shortcut_service: Any,
    spec: AuthShortcutSpec,
    plugin_dir: str | None,
) -> dict[str, Any]:
    """The payload the frontend needs to RunGame this store's auth shortcut.

    ``launcher_path`` is always returned, even on failure, so the frontend
    can fall back to a temporary shortcut — which is the only thing that
    works during the first session after the VDF is written.
    """
    launcher_path = launcher_path_for(plugin_dir)
    unsigned = await ensure_auth_shortcut(shortcut_service, spec, plugin_dir)
    if unsigned is None:
        return {
            "success": False,
            "error": "auth_shortcut_not_ready",
            "launcher_path": launcher_path,
        }
    return {
        "success": True,
        "appid_unsigned": unsigned,
        "launcher_path": launcher_path,
        "launch_wait_ms": spec.launch_wait_ms,
    }
