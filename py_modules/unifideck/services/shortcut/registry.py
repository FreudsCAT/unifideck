"""Persistent shortcuts registry — ``{store:game_id → appid}`` across restarts.

A single JSON file at ``~/.local/share/unifideck/shortcuts_registry.json``
maps each Unifideck-managed game (``"<store>:<game_id>"``) to the
deterministic Steam AppID we assigned when the shortcut was first
created. The file lives in user data, so it survives plugin
uninstall/reinstall — that's what lets us *reclaim* an orphaned
``shortcuts.vdf`` entry after Steam mangled its LaunchOptions or
tags: we look up the registered AppID and reuse it, preserving the
artwork Steam already cached for that ID.

Ported from ``staging:py_modules/unifideck/shortcuts/shortcuts_manager.py``
(``load_shortcuts_registry`` / ``save_shortcuts_registry`` /
``register_shortcut`` family).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path(
    "~/.local/share/unifideck/shortcuts_registry.json",
).expanduser()


def load_registry(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the parsed registry, or an empty dict on any failure.

    Corrupt file / missing file / unreadable file all degrade to
    ``{}`` so callers don't need to handle the load path.
    """
    p = path or DEFAULT_REGISTRY_PATH
    try:
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[ShortcutsRegistry] load failed (%s): %s", p, e)
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(
    registry: dict[str, dict[str, Any]], path: Path | None = None,
) -> bool:
    """Persist ``registry`` to disk. Returns ``True`` on success."""
    p = path or DEFAULT_REGISTRY_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
    except OSError as e:
        logger.warning("[ShortcutsRegistry] save failed (%s): %s", p, e)
        return False
    return True


def register(
    registry: dict[str, dict[str, Any]],
    launch_options: str,
    appid: int,
    title: str,
) -> dict[str, Any]:
    """Add or update an entry; returns the entry dict written.

    Mutates ``registry`` in place. Persistence is the caller's
    responsibility (batch writes amortise the JSON cost).
    """
    appid_unsigned = appid if appid >= 0 else appid + 2 ** 32
    entry: dict[str, Any] = {
        "appid": appid,
        "appid_unsigned": appid_unsigned,
        "title": title,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    registry[launch_options] = entry
    return entry


def get_registered_appid(
    registry: dict[str, dict[str, Any]], launch_options: str,
) -> int | None:
    """Look up the AppID previously assigned to ``launch_options``."""
    entry = registry.get(launch_options)
    if not isinstance(entry, dict):
        return None
    appid = entry.get("appid")
    return appid if isinstance(appid, int) else None


__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "get_registered_appid",
    "load_registry",
    "register",
    "save_registry",
]
