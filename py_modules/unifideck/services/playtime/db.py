"""Playtime DB — SQLite-backed session store.

OP-18b | py_modules/unifideck/services/playtime/db.py

``PlaytimeDB`` is the persistence layer behind ``PlaytimeService``.
The DB schema is two tables:

* ``sessions`` — one row per recorded session (id, store, game_id,
  start_ts, end_ts, duration_s);
* ``totals``  — one row per (store, game_id) holding the aggregate
  (total_s, session_count, last_played), kept in sync with
  ``sessions`` by the same transaction that inserts new sessions.

Plus an ``idx_sessions_game`` index on (store, game_id) to keep
per-game session-history queries fast as the DB grows.

The DB is opened with the default sqlite3 settings (no WAL) — the
service only writes from a single thread (the async loop's thread
executor) so write concurrency isn't a concern, and the queries
are simple enough that PRAGMA tuning doesn't measurably help.

The two tables are populated by ``insert_session`` in a single
transaction so the per-game totals can never drift from the
underlying sessions on a crash.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast


class PlaytimeDB:
    """SQLite wrapper holding playtime sessions and per-game totals."""

    def __init__(self, db_path: str) -> None:
        """Open (or create) the SQLite DB and initialise the schema.

        Args:
            db_path: filesystem path to the SQLite file. Created if
                absent.
        """
        self._conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the schema if absent — idempotent.

        Creates the ``sessions`` and ``totals`` tables plus the
        ``idx_sessions_game`` index. All three statements use
        ``IF NOT EXISTS`` so the method is safe to call on every
        open (which it is, from ``__init__``).
        """
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
        """Close the underlying SQLite connection.

        Called from ``PlaytimeService.stop`` after every pending
        session has been flushed. Calling DB methods after this
        will raise ``sqlite3.ProgrammingError``.
        """
        self._conn.close()

    def insert_session(
        self,
        store: str,
        game_id: str,
        start_ts: float,
        end_ts: float,
        duration: int,
    ) -> None:
        """Append a session row and update the matching totals row atomically.

        The ``with self._conn:`` block opens a single transaction —
        if either INSERT raises, both are rolled back so the totals
        never drift from the sessions.

        The ``totals`` UPSERT uses ``ON CONFLICT(store, game_id) DO
        UPDATE`` to increment ``total_s`` and ``session_count`` if
        the (store, game_id) row already exists, or insert a fresh
        row otherwise.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            start_ts: POSIX timestamp of session start.
            end_ts: POSIX timestamp of session end.
            duration: session duration in seconds (typically
                ``int(end_ts - start_ts)``).
        """
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

    def fetch_total(self, store: str, game_id: str) -> tuple | None:
        """Return the totals row for one game, or None if absent.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``(total_s, session_count, last_played)`` tuple, or
            ``None`` if the game has no recorded sessions.
        """
        cur = self._conn.execute(
            "SELECT total_s, session_count, last_played "
            "FROM totals WHERE store=? AND game_id=?",
            (store, game_id),
        )
        return cast("tuple[Any, ...] | None", cur.fetchone())

    def fetch_all_totals(self) -> list[tuple]:
        """Return every (store, game_id) totals row, most-recent first.

        The result is ordered by ``last_played DESC`` so the caller
        can render a "recently played" list directly without an
        extra sort pass.

        Returns:
            List of ``(store, game_id, total_s, session_count,
            last_played)`` tuples.
        """
        cur = self._conn.execute(
            "SELECT store, game_id, total_s, session_count, "
            "last_played FROM totals ORDER BY last_played DESC",
        )
        return cur.fetchall()

    def fetch_sessions(self, store: str, game_id: str, limit: int) -> list[tuple]:
        """Return the most-recent ``limit`` sessions for one game.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            limit: max rows to return, newest first.

        Returns:
            List of ``(start_ts, end_ts, duration_s)`` tuples
            ordered by ``start_ts DESC``.
        """
        cur = self._conn.execute(
            "SELECT start_ts, end_ts, duration_s FROM sessions "
            "WHERE store=? AND game_id=? ORDER BY start_ts DESC "
            "LIMIT ?",
            (store, game_id, limit),
        )
        return cur.fetchall()
