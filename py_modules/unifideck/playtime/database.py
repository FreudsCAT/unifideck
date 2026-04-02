"""SQLite database connection management for activity tracking."""

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from .migrations import run_migrations

logger = logging.getLogger("unifideck")

# Default data directory (consistent with games.map, shortcuts_registry.json, etc.)
DEFAULT_DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
DB_FILENAME = "activity.db"

# Minimum retry delay for SQLITE_BUSY
BUSY_RETRY_DELAY = 0.05  # 50ms
BUSY_MAX_RETRIES = 3


class ActivityDatabase:
    """Manages the SQLite activity tracking database.

    Uses WAL mode for concurrent read access (future companion app).
    All timestamps are ISO 8601 UTC.
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.db_path = os.path.join(self.data_dir, DB_FILENAME)
        self.conn: Optional[sqlite3.Connection] = None
        self.schema_version: int = 0

    def open(self) -> int:
        """Open the database connection and run migrations.

        Returns:
            Current schema version after migrations.
        """
        os.makedirs(self.data_dir, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row

        # Configure for performance and safety
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA cache_size=20000")

        # Run migrations
        self.schema_version = run_migrations(self.conn)

        logger.info(
            f"[PLAYTIME] Database opened: {self.db_path} (schema v{self.schema_version})"
        )
        return self.schema_version

    def close(self):
        """Close the database connection."""
        if self.conn:
            try:
                self.conn.close()
                logger.info("[PLAYTIME] Database closed")
            except Exception as e:
                logger.error(f"[PLAYTIME] Error closing database: {e}")
            finally:
                self.conn = None

    def execute(
        self, sql: str, params: Tuple = (), retry: bool = True
    ) -> sqlite3.Cursor:
        """Execute a SQL statement with optional busy retry.

        Args:
            sql: SQL statement to execute.
            params: Parameters for the SQL statement.
            retry: Whether to retry on SQLITE_BUSY.

        Returns:
            sqlite3.Cursor with results.
        """
        if not self.conn:
            raise RuntimeError("[PLAYTIME] Database not open")

        for attempt in range(BUSY_MAX_RETRIES if retry else 1):
            try:
                cursor = self.conn.execute(sql, params)
                self.conn.commit()
                return cursor
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < BUSY_MAX_RETRIES - 1:
                    time.sleep(BUSY_RETRY_DELAY * (attempt + 1))
                    continue
                raise

    def execute_many(
        self, sql: str, params_list: List[Tuple], retry: bool = True
    ) -> sqlite3.Cursor:
        """Execute a SQL statement with multiple parameter sets.

        Args:
            sql: SQL statement to execute.
            params_list: List of parameter tuples.
            retry: Whether to retry on SQLITE_BUSY.

        Returns:
            sqlite3.Cursor with results.
        """
        if not self.conn:
            raise RuntimeError("[PLAYTIME] Database not open")

        for attempt in range(BUSY_MAX_RETRIES if retry else 1):
            try:
                cursor = self.conn.executemany(sql, params_list)
                self.conn.commit()
                return cursor
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < BUSY_MAX_RETRIES - 1:
                    time.sleep(BUSY_RETRY_DELAY * (attempt + 1))
                    continue
                raise

    def query(self, sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
        """Execute a read-only query and return all rows.

        Args:
            sql: SQL query to execute.
            params: Parameters for the query.

        Returns:
            List of sqlite3.Row objects.
        """
        if not self.conn:
            raise RuntimeError("[PLAYTIME] Database not open")
        cursor = self.conn.execute(sql, params)
        return cursor.fetchall()

    def query_one(self, sql: str, params: Tuple = ()) -> Optional[sqlite3.Row]:
        """Execute a read-only query and return the first row.

        Args:
            sql: SQL query to execute.
            params: Parameters for the query.

        Returns:
            sqlite3.Row or None.
        """
        if not self.conn:
            raise RuntimeError("[PLAYTIME] Database not open")
        cursor = self.conn.execute(sql, params)
        return cursor.fetchone()

    # ─── Game Registry ──────────────────────────────────────────────

    def get_or_create_game(
        self,
        store: str,
        store_game_id: str,
        title: str,
        steam_app_id: Optional[int] = None,
        real_steam_appid: Optional[int] = None,
        ownership_type: Optional[str] = None,
    ) -> int:
        """Get or create a game in the registry.

        Uses (store, store_game_id) as canonical identity. If the game exists
        but steam_app_id has changed (e.g., after registry loss), updates it.

        Args:
            store: Store name (epic, gog, amazon, ubisoft, microsoft).
            store_game_id: Store-specific game ID.
            title: Game title.
            steam_app_id: Non-Steam shortcut appId (may change).
            real_steam_appid: Real Steam store appId for ProtonDB.
            ownership_type: owned/free/subscription/xcloud.

        Returns:
            The games.id (integer primary key).
        """
        # Try to find existing game
        row = self.query_one(
            "SELECT id, steam_app_id FROM games WHERE store = ? AND store_game_id = ?",
            (store, store_game_id),
        )

        if row:
            game_id = row["id"]
            # Update steam_app_id if it changed (e.g., after registry rebuild)
            if steam_app_id is not None and row["steam_app_id"] != steam_app_id:
                self.execute(
                    "UPDATE games SET steam_app_id = ?, title = ?, last_synced_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                    (steam_app_id, title, game_id),
                )
                logger.info(
                    f"[PLAYTIME] Updated steam_app_id for {store}:{store_game_id} "
                    f"({row['steam_app_id']} → {steam_app_id})"
                )
            return game_id

        # Create new game
        cursor = self.execute(
            """INSERT INTO games (store, store_game_id, steam_app_id, real_steam_appid, title, ownership_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (store, store_game_id, steam_app_id, real_steam_appid, title, ownership_type),
        )
        game_id = cursor.lastrowid
        logger.info(
            f"[PLAYTIME] Registered game: {title} ({store}:{store_game_id}, db_id={game_id})"
        )
        return game_id

    def find_game_by_steam_app_id(self, steam_app_id: int) -> Optional[Dict[str, Any]]:
        """Look up a game by its Steam shortcut appId.

        Args:
            steam_app_id: The non-Steam shortcut appId.

        Returns:
            Dict with game info or None if not found.
        """
        row = self.query_one(
            "SELECT id, store, store_game_id, title, steam_app_id, real_steam_appid, ownership_type FROM games WHERE steam_app_id = ?",
            (steam_app_id,),
        )
        if row:
            return dict(row)
        return None

    def update_game_steam_app_id(self, game_id: int, steam_app_id: int):
        """Update the steam_app_id for a game (after registry rebuild)."""
        self.execute(
            "UPDATE games SET steam_app_id = ? WHERE id = ?",
            (steam_app_id, game_id),
        )
