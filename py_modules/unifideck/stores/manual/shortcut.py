"""Ad-hoc Steam shortcut creation for manual games.

py_modules/unifideck/stores/manual/shortcut.py

Every other store gets its shortcuts from the library-sync reconcile
pass. A manual game cannot wait for that: the Manual Install flow needs
the shortcut (and its games.map row) IMMEDIATELY so the frontend can
RunGame the installer, minutes before the next sync would have created
it. Same VDF write path as ``stores/shared/auth_shortcut.py`` — read
from disk (Steam may have flushed over our cached copy), append one
entry shaped exactly like reconcile's ``_build_shortcut_entry``, write
back. Reconcile then adopts the entry on the next sync (same appid,
same LaunchOptions) instead of duplicating it.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from unifideck.services.shortcut.games_map import UNIFIDECK_TAG
from unifideck.services.shortcut.launch_options import get_full_id
from unifideck.stores.shared.auth_shortcut import launcher_path_for

logger = logging.getLogger(__name__)

STORE_NAME = "manual"


def _build_game_entry(
    *, appid: int, title: str, launcher_path: str,
    install_path: str, launch_options: str,
) -> dict[str, Any]:
    """A visible, installed game entry — field set mirrors reconcile."""
    return {
        "appid": appid,
        "AppName": title,
        "Exe": f'"{launcher_path}"',
        "StartDir": f'"{install_path}"' if install_path else '""',
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {"0": UNIFIDECK_TAG, "1": STORE_NAME, "2": ""},
    }


async def _read_from_disk(shortcut_service: Any) -> dict[str, Any]:
    """The VDF as Steam last flushed it (see auth_shortcut for why)."""
    try:
        data = await shortcut_service.read_shortcuts(from_disk=True)
    except TypeError:
        data = await shortcut_service.read_shortcuts()
    return dict(data) if isinstance(data, dict) else {"shortcuts": {}}


async def ensure_manual_game_shortcut(
    shortcut_service: Any,
    *,
    game_id: str,
    title: str,
    install_path: str,
    plugin_dir: str | None,
) -> int | None:
    """Create the manual game's shortcut if absent. Returns its signed appid.

    Never raises — a failure degrades to ``None`` and the caller
    reports the add as failed (nothing to launch without a shortcut).
    """
    if shortcut_service is None:
        logger.warning("[ManualShortcut] no shortcut service available")
        return None
    launcher_path = launcher_path_for(plugin_dir)
    identity = f"{STORE_NAME}:{game_id}"
    try:
        appid = int(shortcut_service.generate_app_id(launcher_path, identity))
        data = await _read_from_disk(shortcut_service)
        shortcuts = data.get("shortcuts", {})
        if not isinstance(shortcuts, dict):
            shortcuts = {}

        for entry in shortcuts.values():
            if not isinstance(entry, dict):
                continue
            if get_full_id(str(entry.get("LaunchOptions") or "")) == identity:
                existing = entry.get("appid")
                logger.info(
                    "[ManualShortcut] %s already in VDF (appid=%s)",
                    identity, existing,
                )
                return existing if isinstance(existing, int) else appid

        indices = [int(k) for k in shortcuts if str(k).isdigit()]
        shortcuts[str(max(indices, default=-1) + 1)] = _build_game_entry(
            appid=appid,
            title=title,
            launcher_path=launcher_path,
            install_path=str(Path(install_path)) if install_path else "",
            launch_options=identity,
        )
        data["shortcuts"] = shortcuts
        await shortcut_service.write_shortcuts(data)
        logger.info("[ManualShortcut] created %s (appid=%d)", identity, appid)
    except Exception:
        logger.exception("[ManualShortcut] creation failed for %s", identity)
        return None
    return appid
