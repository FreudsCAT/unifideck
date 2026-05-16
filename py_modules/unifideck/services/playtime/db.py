"""services/playtime/db.py — SQLite persistence for playtime tracking.

Bare ``sqlite3`` wrapper with hand-rolled migrations. Kept in the
plugin runtime (not async) because every call is local-disk and
the dataset stays tiny.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --- Models ---
@dataclass
class PlaySessionResult:
    id: int
    game_id: int
    started_at: str
    ended_at: str | None
    duration_secs: int | None
    end_reason: str
    title: str
    store: str
    proton_tool: str | None = None
    is_manual: bool = False

@dataclass
class GameStatsResult:
    game_id: int
    title: str
    store: str
    steam_app_id: int | None
    total_secs: int
    total_sessions: int
    avg_session_secs: int
    min_session_secs: int | None
    max_session_secs: int
    first_played_at: str | None
    last_played_at: str | None
    current_streak_days: int
    longest_streak_days: int

@dataclass
class DailyTotal:
    date: str
    total_secs: int
    session_count: int
    games_played: int

# --- Migrations ---
def run_migrations(conn: sqlite3.Connection) -> int:
    """Run schema migrations forward. Returns the version applied."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA user_version")
    # ``fetchone()`` returns Any|None; coerce to int.
    current_version = int(cursor.fetchone()[0])

    if current_version < 1:
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                store_game_id TEXT NOT NULL,
                steam_app_id INTEGER,
                real_steam_appid INTEGER,
                title TEXT NOT NULL,
                ownership_type TEXT,
                last_synced_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(store, store_game_id)
            );
            CREATE INDEX idx_games_steam_app_id ON games(steam_app_id);

            CREATE TABLE IF NOT EXISTS play_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                steam_user_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_secs INTEGER,
                end_reason TEXT NOT NULL,
                proton_tool TEXT,
                is_manual BOOLEAN DEFAULT 0,
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_sessions_game_id ON play_sessions(game_id);
            CREATE INDEX idx_sessions_started ON play_sessions(started_at);

            CREATE TABLE IF NOT EXISTS daily_stats (
                game_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                total_secs INTEGER NOT NULL DEFAULT 0,
                session_count INTEGER NOT NULL DEFAULT 0,
                longest_session_secs INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(game_id, date),
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS game_stats (
                game_id INTEGER PRIMARY KEY,
                total_secs INTEGER NOT NULL DEFAULT 0,
                total_sessions INTEGER NOT NULL DEFAULT 0,
                avg_session_secs INTEGER NOT NULL DEFAULT 0,
                min_session_secs INTEGER,
                max_session_secs INTEGER NOT NULL DEFAULT 0,
                first_played_at TEXT,
                last_played_at TEXT,
                current_streak_days INTEGER NOT NULL DEFAULT 0,
                longest_streak_days INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                game_id INTEGER,
                event_type TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE SET NULL
            );
        """)
        cursor.execute("PRAGMA user_version = 1")
        conn.commit()
        return 1
    return current_version

# --- Database ---
class ActivityDatabase:
    def __init__(self, db_path: str) -> None:
        """Initialise paths but defer the connect to ``open()``."""
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def _require_conn(self) -> sqlite3.Connection:
        """Return the live connection or raise.

        Helper that narrows ``self.conn`` from ``Connection | None``
        to ``Connection`` for mypy strict, with a clear runtime
        error if a caller forgets ``open()``. Without this, every
        method below would need its own ``if self.conn is None``
        guard or a ``# type: ignore`` on every ``self.conn.X`` call.
        """
        if self.conn is None:
            raise RuntimeError(
                "ActivityDatabase used before open() — "
                "call open() before any query/execute/commit.",
            )
        return self.conn

    def open(self) -> int:
        """Open the connection, configure PRAGMAs, run migrations."""
        parent = str(Path(self.db_path).parent)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        return run_migrations(self.conn)

    def close(self) -> None:
        """Close the connection if open. Idempotent (safe to re-call)."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute(
        self, sql: str, params: tuple[Any, ...] = (),
    ) -> sqlite3.Cursor:
        """Execute SQL with parameters. Requires ``open()`` first."""
        return self._require_conn().execute(sql, params)

    def query(
        self, sql: str, params: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        """Run a SELECT, return all rows. Requires ``open()`` first."""
        return self._require_conn().execute(sql, params).fetchall()

    def query_one(
        self, sql: str, params: tuple[Any, ...] = (),
    ) -> sqlite3.Row | None:
        """Run a SELECT, return the first row or None."""
        # Cast through Any because ``fetchone`` is typed as
        # ``Any`` by the stubs; we know it's ``Row | None`` here.
        return self._require_conn().execute(sql, params).fetchone()  # type: ignore[no-any-return]

    def get_or_create_game(
        self, store: str, store_game_id: str, title: str, steam_app_id: int,
    ) -> int:
        """Upsert game record. Returns the game's primary key."""
        row = self.query_one(
            "SELECT id FROM games WHERE store = ? AND store_game_id = ?",
            (store, store_game_id),
        )
        conn = self._require_conn()
        if row:
            self.execute(
                "UPDATE games SET steam_app_id = ? WHERE id = ?",
                (steam_app_id, row["id"]),
            )
            conn.commit()
            # ``row["id"]`` is typed as ``Any`` by sqlite3 stubs.
            return int(row["id"])

        cursor = self.execute(
            "INSERT INTO games (store, store_game_id, steam_app_id, title) "
            "VALUES (?, ?, ?, ?)",
            (store, store_game_id, steam_app_id, title),
        )
        conn.commit()
        # ``lastrowid`` is ``int | None``; ``None`` only when the
        # last execute wasn't INSERT/UPDATE/DELETE, which can't
        # happen here.
        last_id = cursor.lastrowid
        if last_id is None:
            raise RuntimeError(
                "INSERT into games returned no lastrowid — "
                "should not happen on a successful insert.",
            )
        return last_id
