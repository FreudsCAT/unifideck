"""Event recording for game lifecycle, device, and activity events."""

import json
import logging
from typing import Any, Dict, Optional

from .database import ActivityDatabase
from .models import DeviceEventType, GameEventType

logger = logging.getLogger("unifideck")


class EventRecorder:
    """Facade for writing to game_events, device_events, and activity_log tables."""

    def __init__(self, db: ActivityDatabase):
        self.db = db

    def record_game_event(
        self,
        game_id: int,
        event_type: GameEventType,
        details: Optional[Dict[str, Any]] = None,
        source: str = "system",
    ):
        """Record a game lifecycle event.

        Args:
            game_id: The games.id foreign key.
            event_type: Type of game event.
            details: Optional JSON-serializable details dict.
            source: Event source (user/system/sync/backend).
        """
        try:
            details_json = json.dumps(details) if details else None
            self.db.execute(
                """INSERT INTO game_events (game_id, event_type, details_json, source)
                   VALUES (?, ?, ?, ?)""",
                (game_id, event_type.value, details_json, source),
            )
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to record game event {event_type.value}: {e}")

    def record_device_event(
        self,
        event_type: DeviceEventType,
        details: Optional[Dict[str, Any]] = None,
        steam_user_id: Optional[str] = None,
    ):
        """Record a device/plugin lifecycle event.

        Args:
            event_type: Type of device event.
            details: Optional JSON-serializable details dict.
            steam_user_id: Steam account ID if relevant.
        """
        try:
            details_json = json.dumps(details) if details else None
            self.db.execute(
                """INSERT INTO device_events (event_type, details_json, steam_user_id)
                   VALUES (?, ?, ?)""",
                (event_type.value, details_json, steam_user_id),
            )
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to record device event {event_type.value}: {e}")

    def record_activity(
        self,
        category: str,
        action: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Record a generic activity log entry.

        Args:
            category: Log category (settings/artwork/compat/error/import/migration).
            action: Description of the action.
            details: Optional JSON-serializable details dict.
        """
        try:
            details_json = json.dumps(details) if details else None
            self.db.execute(
                """INSERT INTO activity_log (category, action, details_json)
                   VALUES (?, ?, ?)""",
                (category, action, details_json),
            )
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to record activity {category}/{action}: {e}")

    def record_store_connection(
        self,
        store: str,
        connected: bool,
        account_name: Optional[str] = None,
        steam_user_id: Optional[str] = None,
    ):
        """Record a store account connection or disconnection.

        Args:
            store: Store name (epic/gog/amazon/ubisoft/microsoft).
            connected: True if connecting, False if disconnecting.
            account_name: Display name if available.
            steam_user_id: Steam account ID.
        """
        try:
            if connected:
                # Upsert: update if exists, insert if new
                existing = self.db.query_one(
                    "SELECT id FROM store_accounts WHERE store = ? AND steam_user_id = ?",
                    (store, steam_user_id or ""),
                )
                if existing:
                    self.db.execute(
                        """UPDATE store_accounts
                           SET is_active = 1, account_name = ?,
                               connected_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                               disconnected_at = NULL
                           WHERE id = ?""",
                        (account_name, existing["id"]),
                    )
                else:
                    self.db.execute(
                        """INSERT INTO store_accounts (store, account_name, steam_user_id)
                           VALUES (?, ?, ?)""",
                        (store, account_name, steam_user_id or ""),
                    )
                self.record_device_event(
                    DeviceEventType.STORE_CONNECTED,
                    details={"store": store, "account_name": account_name},
                    steam_user_id=steam_user_id,
                )
            else:
                self.db.execute(
                    """UPDATE store_accounts
                       SET is_active = 0,
                           disconnected_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE store = ? AND steam_user_id = ? AND is_active = 1""",
                    (store, steam_user_id or ""),
                )
                self.record_device_event(
                    DeviceEventType.STORE_DISCONNECTED,
                    details={"store": store},
                    steam_user_id=steam_user_id,
                )
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to record store connection {store}: {e}")
