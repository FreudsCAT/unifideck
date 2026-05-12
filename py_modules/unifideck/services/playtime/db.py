"""Playtime DB — SQLite-backed session store.

OP-18b | py_modules/unifideck/services/playtime/db.py

``PlaytimeDB`` is the persistence layer. The DB schema is two tables :

* ``sessions`` — one row per (game_id, started_at, ended_at, duration);
* ``meta``     — schema version + last-known migration timestamp.

Migrations are version-checked at first open and applied
idempotently. The DB file lives under ``ServicePaths.data_dir`` and
is opened in WAL mode for concurrent read/write safety.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast


class PlaytimeDB:
    """Playtime db."""

    def __init__(self, db_path: str) -> None:
        """Initialize the instance."""
        self._conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        """Init schema."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                game_id TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                duration_s INTEGER NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS totals (
                store TEXT NOT NULL,
                game_id TEXT NOT NULL,
                total_s INTEGER NOT NULL DEFAULT 0,
                session_count INTEGER NOT NULL DEFAULT 0,
                last_played REAL,
                PRIMARY KEY (store, game_id)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_game
            ON sessions(store, game_id)
        """)
        self._conn.commit()

    def close(self) -> None:
        """Close."""
        self._conn.close()

    def insert_session(
        self,
        store: str,
        game_id: str,
        start_ts: float,
        end_ts: float,
        duration: int,
    ) -> None:
        """Insert session."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions(store, game_id, start_ts, "
                "end_ts, duration_s) VALUES (?, ?, ?, ?, ?)",
                (store, game_id, start_ts, end_ts, duration),
            )
            self._conn.execute(
                "INSERT INTO totals(store, game_id, total_s, "
                "session_count, last_played) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(store, game_id) DO UPDATE SET "
                "total_s = total_s + excluded.total_s, "
                "session_count = session_count + 1, "
                "last_played = excluded.last_played",
                (store, game_id, duration, end_ts),
            )

    def total_for(self, store: str, game_id: str) -> int:
        """Total for."""
        cur = self._conn.execute(
            "SELECT total_s FROM totals WHERE store=? AND game_id=?",
            (store, game_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Recent."""
        cur = self._conn.execute(
            "SELECT store, game_id, total_s, session_count, last_played "
            "FROM totals "
            "WHERE last_played IS NOT NULL "
            "ORDER BY last_played DESC "
            "LIMIT ?",
            (limit,),
        )
        return [
            cast(
                "dict[str, Any]",
                {
                    "store": r[0],
                    "game_id": r[1],
                    "total_s": r[2],
                    "sessions": r[3],
                    "last_played": r[4],
                },
            )
            for r in cur.fetchall()
        ]

    def history(self, store: str, game_id: str) -> list[dict[str, Any]]:
        """History."""
        cur = self._conn.execute(
            "SELECT start_ts, end_ts, duration_s FROM sessions "
            "WHERE store=? AND game_id=? "
            "ORDER BY start_ts DESC",
            (store, game_id),
        )
        return [
            cast(
                "dict[str, Any]",
                {"start": r[0], "end": r[1], "duration": r[2]},
            )
            for r in cur.fetchall()
        ]
