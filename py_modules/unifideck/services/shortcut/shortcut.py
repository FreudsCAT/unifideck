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
                service._shortcuts = {"shortcuts": {}}  # type: ignore[unreachable]  # guard 'if not isinstance(_shortcuts, dict)'
            elif "shortcuts" not in service._shortcuts:
                service._shortcuts["shortcuts"] = {}

            # Append new entry
            shortcuts_dict = service._shortcuts["shortcuts"]
            new_key = str(len(shortcuts_dict))
            shortcuts_dict[new_key] = entry
            changed = True

            # Re-read the appid from the entry dict. ``entry.get`` is
            # typed Any | None (entry is a dict[str, Any] from VDF
            # parsing); coerce to int with a 0 fallback so the bus
            # payload always has the expected ``int`` field.
            raw_app_id = entry.get("appid")
            app_id = int(raw_app_id) if isinstance(raw_app_id, int) else 0

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

    Refactor history (2026-05-14): was a single function at
    CC=20 — the entry-classification logic (is it an auth entry
    for this store? is it correctly shaped?) was inlined inside
    the loop with three guard layers plus the two ``any(...)``
    expressions for tag detection. Pulled the classifications
    into ``_is_auth_entry_for_store`` and ``_is_canonical_shape``
    so the loop body reads as ``if auth_entry: keep or delete``.
    """
    if not isinstance(service._shortcuts, dict) or "shortcuts" not in service._shortcuts:
        return None, False

    shortcuts_dict = service._shortcuts["shortcuts"]
    if not isinstance(shortcuts_dict, dict):
        return None, False

    canonical_entry: dict[str, Any] | None = None
    keys_to_delete: list[str] = []
    auth_tag = f"auth-{store}"

    for key, entry in shortcuts_dict.items():
        if not _is_auth_entry_for_store(entry, auth_tag):
            continue
        # First well-shaped match wins ; everything else is a
        # duplicate or malformed copy and gets queued for delete.
        if canonical_entry is None and _is_canonical_shape(
            entry, launcher_path, store,
        ):
            canonical_entry = entry
        else:
            keys_to_delete.append(key)

    changed = bool(keys_to_delete)
    for key in keys_to_delete:
        del shortcuts_dict[key]

    return canonical_entry, changed


def _is_auth_entry_for_store(entry: Any, auth_tag: str) -> bool:
    """Whether ``entry`` is tagged as an auth-shortcut for our store.

    Filters out :

        * Non-dict entries (corrupt VDF).
        * Entries with no ``tags`` dict (user-created shortcuts).
        * Entries that don't carry BOTH ``_UNIFIDECK_TAG`` and
          the store-specific ``auth-<store>`` tag — we don't
          want to touch other stores' auth entries or regular
          game shortcuts.
    """
    if not isinstance(entry, dict):
        return False
    tags = entry.get("tags", {})
    if not isinstance(tags, dict):
        return False
    tag_values = list(tags.values())
    has_unifideck_tag = any(t == _UNIFIDECK_TAG for t in tag_values)
    has_auth_tag = any(t == auth_tag for t in tag_values)
    return has_unifideck_tag and has_auth_tag


def _is_canonical_shape(
    entry: dict[str, Any], launcher_path: str, store: str,
) -> bool:
    """Whether the auth entry has the expected ``Exe`` and ``LaunchOptions``.

    A canonical Unifideck auth shortcut points at the bundled
    launcher with canonical launch options. A drift on either field means
    the entry is stale and the caller will replace it.
    """
    expected_opts = f"{store}:{'ms' if store == 'microsoft' else store}-auth UNIFIDECK_{store.upper()}_ACTION=auth"
    return (
        entry.get("Exe") == launcher_path
        and entry.get("LaunchOptions") == expected_opts
    )


def _build_auth_entry(
    store: str,
    launcher_path: str,
    title: str,
    app_id: int,
) -> dict[str, Any]:
    """Build the canonical VDF entry dict for an auth shortcut.

    Populates ``appid``, ``AppName`` (= ``title``), ``Exe``
    (launcher path), ``LaunchOptions``, ``tags`` including
    ``_UNIFIDECK_TAG`` and an ``auth-<store>`` marker, and
    ``IsHidden=1``.
    """
    expected_opts = f"{store}:{'ms' if store == 'microsoft' else store}-auth UNIFIDECK_{store.upper()}_ACTION=auth"
    return {
        "appid": app_id,
        "AppName": title,
        "Exe": launcher_path,
        "StartDir": "",
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": expected_opts,
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
