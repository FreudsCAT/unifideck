"""services/shortcut/shortcut.py — OAuth sign-in shortcut policy.

Several stores (Amazon, Microsoft, GOG) use a "sign-in via
browser" pattern where the user launches a Steam shortcut that
opens a Chromium window on the store's OAuth page. These
shortcuts need a specific LaunchOptions format in shortcuts.vdf
so the Python dispatcher knows to spawn the browser instead of
a game.

Free function (not method) because ``ShortcutService`` was
already large. Same pattern as
``services/security/device_reset.py``.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Result

if TYPE_CHECKING:
    from .service import ShortcutService

logger = logging.getLogger(__name__)

# Signature tag added to ``tags`` so we can identify
# Unifideck-managed shortcuts for cleanup. Kept in sync with
# the same constant in service.py.
_UNIFIDECK_TAG = "Unifideck"


async def build_auth_shortcut(
    service: ShortcutService,
    store: str,
    launcher_path: str,
    title: str,
) -> Result:
    """Create a hidden Steam shortcut for store OAuth login.

    Three phases: validate inputs (empty ``launcher_path`` or
    ``title`` → fail early); find canonical + prune malformed
    duplicates (delegates to ``_prune_malformed_duplicates``);
    append a new canonical entry if none exists. Save only if
    something changed. Always emits ``SHORTCUT_CREATED`` for
    observability. Returns a populated ``Result``.
    """
    if not launcher_path or not title:
        return Result(success=False, error="launcher_path and title are required")

    try:
        await service._load_shortcuts()

        canonical, changed = _prune_malformed_duplicates(service, store, launcher_path)

        if not canonical:
            app_id = service.generate_app_id(launcher_path, title)
            entry = _build_auth_entry(store, launcher_path, title, app_id)

            # Ensure shortcuts is a dict with 'shortcuts' key
            if not isinstance(service._shortcuts, dict):
                service._shortcuts = {"shortcuts": {}}
            elif "shortcuts" not in service._shortcuts:
                service._shortcuts["shortcuts"] = {}

            # Append new entry
            shortcuts_dict = service._shortcuts["shortcuts"]
            new_key = str(len(shortcuts_dict))
            shortcuts_dict[new_key] = entry
            changed = True

            app_id = entry.get("appid")

            if service._bus:
                from unifideck.core.types.events import Events
                await service._bus.emit(
                    Events.SHORTCUT_CREATED,
                    store=store,
                    app_id=app_id,
                    title=title,
                    is_auth=True,
                )

        if changed:
            await service._save_all()

        return Result(success=True)

    except Exception as e:
        logger.exception("[AuthShortcut] failed to build shortcut for %s", store)
        return Result(success=False, error=str(e))


def _prune_malformed_duplicates(
    service: ShortcutService,
    store: str,
    launcher_path: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Scan shortcuts for this store's auth entry.

    Keep the correctly-shaped one, delete the rest. Returns
    ``(canonical, changed)`` — canonical is the matching entry
    (or None if nothing matched); changed signals whether any
    malformed duplicates were removed (caller must persist).
    """
    if not isinstance(service._shortcuts, dict) or "shortcuts" not in service._shortcuts:
        return None, False

    shortcuts_dict = service._shortcuts["shortcuts"]
    if not isinstance(shortcuts_dict, dict):
        return None, False

    canonical_entry = None
    keys_to_delete = []
    changed = False
    auth_tag = f"auth-{store}"

    for key, entry in shortcuts_dict.items():
        if not isinstance(entry, dict):
            continue

        tags = entry.get("tags", {})
        if not isinstance(tags, dict):
            continue

        # Check if it has our tags
        has_tags = any(t == _UNIFIDECK_TAG for t in tags.values()) and \
                   any(t == auth_tag for t in tags.values())

        if has_tags:
            # Check shape
            is_valid = entry.get("Exe") == launcher_path and \
                       entry.get("LaunchOptions") == f"auth {store}"

            if is_valid and canonical_entry is None:
                # Keep the first valid one we find
                canonical_entry = entry
            else:
                # Mark duplicates or malformed ones for deletion
                keys_to_delete.append(key)

    if keys_to_delete:
        changed = True
        for key in keys_to_delete:
            del shortcuts_dict[key]

    return canonical_entry, changed


def _build_auth_entry(
    store: str,
    launcher_path: str,
    title: str,
    app_id: int,
) -> dict[str, Any]:
    """Build the canonical VDF entry dict for an auth shortcut.

    Populates ``appid``, ``AppName`` (= ``title``), ``Exe``
    (launcher path), ``LaunchOptions`` (= ``auth <store>``),
    ``tags`` including ``_UNIFIDECK_TAG`` and an ``auth-<store>``
    marker, ``IsHidden=1`` so the shortcut doesn't clutter
    the library.
    """
    return {
        "appid": app_id,
        "AppName": title,
        "Exe": launcher_path,
        "StartDir": "",
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": f"auth {store}",
        "IsHidden": 1,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": int(time.time()),
        "FlatpakAppID": "",
        "tags": {
            "0": _UNIFIDECK_TAG,
            "1": f"auth-{store}",
        },
    }
