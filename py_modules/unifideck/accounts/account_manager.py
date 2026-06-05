"""Steam account-switch detector + data migrator.

Ported from ``staging:py_modules/unifideck/accounts/account_manager.py``.
On the new (mixin) architecture the runtime ``AccountService``
([services/account_service.py]) handles *live* switch detection (polls
``loginusers.vdf``, emits ``ACCOUNT_SWITCHED``); this class handles the
*startup* modal flow the frontend drives via ``check_account_switch`` /
``migrate_account_data``: it compares the active Steam user against the
``last_known_user_id`` persisted in ``settings.json`` and offers to
migrate shortcuts + artwork from the previous account.

Two adaptations from the staging port:

* Steam helpers — staging's ``get_logged_in_steam_user`` / ``_find_steam_path``
  became :func:`unifideck.steam.steam_user.get_active_steam_user`
  (takes a ``Path``) and :func:`unifideck.steam.library.find_steam_path`.
* Shortcut reconciliation — staging called a synchronous
  ``shortcuts_manager.reconcile_shortcuts_from_games_map()``. The new
  ``ShortcutService.reconcile(games)`` is async and game-list driven, so
  the RPC mixin drives it directly; this class only owns artwork
  migration (pure filesystem) plus the detection/gating helpers.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from unifideck.steam.library import find_steam_path
from unifideck.steam.steam_user import get_active_steam_user

logger = logging.getLogger(__name__)

SETTINGS_PATH = os.path.expanduser("~/.local/share/unifideck/settings.json")

# Auth token file locations (shared across all Steam accounts). Existence
# of any one means there's a signed-in store worth migrating/clearing.
AUTH_TOKEN_PATHS: dict[str, str] = {
    "epic": os.path.expanduser("~/.config/legendary/user.json"),
    "gog": os.path.expanduser("~/.config/unifideck/gog_token.json"),
    "gogdl": os.path.expanduser("~/.config/unifideck/gogdl/auth.json"),
    "amazon": os.path.expanduser("~/.config/nile/user.json"),
    "amazon_library": os.path.expanduser("~/.config/nile/library.json"),
    "amazon_installed": os.path.expanduser("~/.config/nile/installed.json"),
    "ubisoft": os.path.expanduser("~/.local/share/unifideck/ubisoft_token.json"),
    "ubisoft_session": os.path.expanduser(
        "~/.local/share/unifideck/ubisoft_upc_session.txt"
    ),
    "microsoft": os.path.expanduser("~/.config/unifideck/microsoft_token.json"),
}

# Shared Unifideck data file (same path on the new branch —
# see services/shortcut/registry.py DEFAULT_REGISTRY_PATH).
REGISTRY_PATH = os.path.expanduser(
    "~/.local/share/unifideck/shortcuts_registry.json"
)


class AccountManager:
    """Manages account-switch detection + migration for the modal flow."""

    def __init__(self) -> None:
        """Resolve the Steam path; init empty switch state."""
        self.steam_path: str | None = find_steam_path()
        self.account_switch_detected = False
        self.previous_user_id: str | None = None
        self.current_user_id: str | None = None

    def detect_account_switch(self) -> bool:
        """Compare the active user to ``last_known_user_id`` in settings.

        Returns ``True`` only when a switch is detected **and** there's
        data to act on (auth tokens exist or the shortcuts registry has
        entries). Guest users are treated like regular accounts.
        """
        self.current_user_id = (
            get_active_steam_user(Path(self.steam_path))
            if self.steam_path
            else None
        )
        if not self.current_user_id:
            logger.warning("[AccountSwitch] Could not detect current Steam user")
            return False

        last_known = self._load_last_known_user()

        if last_known is None:
            logger.info(
                "[AccountSwitch] First run, recording user %s",
                self.current_user_id,
            )
            self.account_switch_detected = False
            return False

        if last_known == self.current_user_id:
            logger.debug(
                "[AccountSwitch] Same user %s, no switch", self.current_user_id
            )
            self.account_switch_detected = False
            return False

        self.previous_user_id = last_known
        logger.info(
            "[AccountSwitch] Account switch detected: %s -> %s",
            last_known,
            self.current_user_id,
        )

        if self.has_active_auth_tokens() or self.has_registry_entries():
            self.account_switch_detected = True
            return True

        logger.info(
            "[AccountSwitch] Switch detected but nothing to migrate — "
            "skipping modal",
        )
        self.account_switch_detected = False
        return False

    def should_show_modal(self) -> bool:
        """True if a switch was detected and there's actionable data."""
        return self.account_switch_detected

    def has_active_auth_tokens(self) -> bool:
        """True if any store auth-token file exists on disk."""
        for store, path in AUTH_TOKEN_PATHS.items():
            if os.path.exists(path):
                logger.debug(
                    "[AccountSwitch] Found auth token for %s: %s", store, path
                )
                return True
        return False

    def has_registry_entries(self) -> bool:
        """True if ``shortcuts_registry.json`` has any entries."""
        try:
            if os.path.exists(REGISTRY_PATH):
                with open(REGISTRY_PATH) as f:
                    registry = json.load(f)
                return len(registry) > 0
        except Exception as e:
            logger.error("[AccountSwitch] Error reading registry: %s", e)
        return False

    def save_current_user(self) -> None:
        """Persist ``current_user_id`` as ``last_known_user_id``.

        Called after :meth:`detect_account_switch` so the *next* launch
        compares against this user (the modal shows once per switch).
        """
        if not self.current_user_id:
            return
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            settings: dict[str, Any] = {}
            if os.path.exists(SETTINGS_PATH):
                with open(SETTINGS_PATH) as f:
                    settings = json.load(f)
            settings["last_known_user_id"] = self.current_user_id
            with open(SETTINGS_PATH, "w") as f:
                json.dump(settings, f, indent=2)
            logger.info(
                "[AccountSwitch] Saved current user %s to settings",
                self.current_user_id,
            )
        except Exception as e:
            logger.error("[AccountSwitch] Error saving current user: %s", e)

    def migrate_artwork(self) -> dict[str, Any]:
        """Copy grid artwork from the previous user's folder to the current.

        Never overwrites existing artwork. Returns
        ``{'copied': int, 'errors': list}``.
        """
        result: dict[str, Any] = {"copied": 0, "errors": []}

        if not (self.previous_user_id and self.current_user_id and self.steam_path):
            result["errors"].append("Missing user IDs or steam path")
            return result

        source_grid = os.path.join(
            self.steam_path, "userdata", self.previous_user_id, "config", "grid"
        )
        target_grid = os.path.join(
            self.steam_path, "userdata", self.current_user_id, "config", "grid"
        )

        if not os.path.isdir(source_grid):
            logger.info(
                "[AccountSwitch] No artwork folder for previous user %s",
                self.previous_user_id,
            )
            return result

        os.makedirs(target_grid, exist_ok=True)
        try:
            for filename in os.listdir(source_grid):
                source_file = os.path.join(source_grid, filename)
                target_file = os.path.join(target_grid, filename)
                if not os.path.isfile(source_file):
                    continue
                if os.path.exists(target_file):
                    continue
                try:
                    shutil.copy2(source_file, target_file)
                    result["copied"] += 1
                except Exception as e:
                    result["errors"].append(f"Failed to copy {filename}: {e}")
            logger.info(
                "[AccountSwitch] Copied %d artwork files from %s to %s",
                result["copied"],
                self.previous_user_id,
                self.current_user_id,
            )
        except Exception as e:
            logger.error("[AccountSwitch] Error migrating artwork: %s", e)
            result["errors"].append(str(e))

        return result

    def _load_last_known_user(self) -> str | None:
        """Read ``last_known_user_id`` from settings; ``None`` if unset."""
        try:
            if os.path.exists(SETTINGS_PATH):
                with open(SETTINGS_PATH) as f:
                    settings = json.load(f)
                return settings.get("last_known_user_id")
        except Exception as e:
            logger.error("[AccountSwitch] Error reading settings: %s", e)
        return None
