"""services/playtime/service.py — Per-game session tracker.

SQLite-backed playtime tracker. Refactor of legacy playtime/
package. Emits ``PLAYTIME_UPDATED`` after each session.
Persistence lives in ``db.py`` (``PlaytimeDB``); this module
owns event wiring + session lifecycle.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ...core.types import Events
from ...event_bus.event_bus_devex import subscribe
from .db import ActivityDatabase

if TYPE_CHECKING:
    from ...event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Sessions shorter than this are ignored as accidental launches.
_MIN_SESSION_SECONDS = 5


class PlaytimeService:
    """SQLite-backed playtime tracker wired to the EventBus."""

    def __init__(self, bus: EventBus, db_path: str) -> None:
        """Store refs, init empty ``_active`` map, and auto_wire."""
        self._bus = bus
        self._db_path = db_path
        self._db: ActivityDatabase | None = None
        self._active: dict[str, dict[str, Any]] = {}
        
        self._bus.auto_wire(self)

    async def start(self) -> None:
        """Open the SQLite database + create tables if missing."""
        if self._db is None:
            self._db = ActivityDatabase(self._db_path)
            self._db.open()

    async def stop(self) -> None:
        """Flush in-flight sessions and close the DB."""
        if self._db is not None:
            # End all active sessions
            keys = list(self._active.keys())
            for key in keys:
                store, game_id = key.split(":", 1)
                await self._end_session(store, game_id, end_reason="plugin_unload")

            self._db.close()
            self._db = None

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs: Any) -> None:
        """Record session start in ``_active`` under ``store:game_id``."""
        if self._db is None:
            return

        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        title = kwargs.get("title", "")
        app_id = kwargs.get("app_id", 0)

        if not store or not game_id:
            return

        key = f"{store}:{game_id}"
        
        if key in self._active:
            logger.warning("[PlaytimeService] Session already active for %s", key)
            return

        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        # Get or create game ID
        game_db_id = self._db.get_or_create_game(store, game_id, title, app_id)

        # Insert session with ended_at=NULL (active)
        cursor = self._db.execute(
            """INSERT INTO play_sessions
               (game_id, started_at, end_reason)
               VALUES (?, ?, 'unknown')""",
            (game_db_id, now_iso),
        )
        self._db.conn.commit()
        row_id = cursor.lastrowid

        self._active[key] = {
            "game_db_id": game_db_id,
            "title": title,
            "started_at": now,
            "db_row_id": row_id,
            "total_sleep_secs": 0.0,
            "suspended_at": None,
        }

        logger.info("[PlaytimeService] Session started: %s (%s)", title, key)

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """Delegate to ``_end_session(store, game_id)``."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        
        if store and game_id:
            await self._end_session(store, game_id, end_reason="normal")

    @subscribe(Events.SUSPEND)
    async def _on_suspend(self, **kwargs: Any) -> None:
        """Pause the clock for all active sessions."""
        now = datetime.now(timezone.utc)
        count = 0
        for session in self._active.values():
            if session["suspended_at"] is None:
                session["suspended_at"] = now
                count += 1
        if count > 0:
            logger.info("[PlaytimeService] Suspended %d active session(s)", count)

    @subscribe(Events.RESUME)
    async def _on_resume(self, **kwargs: Any) -> None:
        """Resume the clock for all suspended sessions."""
        now = datetime.now(timezone.utc)
        count = 0
        for session in self._active.values():
            if session["suspended_at"] is not None:
                sleep_duration = (now - session["suspended_at"]).total_seconds()
                session["total_sleep_secs"] += sleep_duration
                session["suspended_at"] = None
                count += 1
        if count > 0:
            logger.info("[PlaytimeService] Resumed %d active session(s)", count)

    async def get_playtime(self, store: str, game_id: str) -> dict[str, Any]:
        """Return cumulative playtime for a single game."""
        if self._db is None:
            return {}

        key = f"{store}:{game_id}"
        is_active = key in self._active

        row = self._db.query_one(
            """SELECT gs.total_secs, gs.total_sessions, gs.last_played_at,
                      gs.current_streak_days, gs.longest_streak_days
               FROM games g
               JOIN game_stats gs ON g.id = gs.game_id
               WHERE g.store = ? AND g.store_game_id = ?""",
            (store, game_id)
        )

        if row:
            return {
                "total_seconds": row["total_secs"],
                "session_count": row["total_sessions"],
                "last_played": row["last_played_at"],
                "current_streak": row["current_streak_days"],
                "longest_streak": row["longest_streak_days"],
                "is_active": is_active,
            }

        return {
            "total_seconds": 0,
            "session_count": 0,
            "last_played": None,
            "current_streak": 0,
            "longest_streak": 0,
            "is_active": is_active,
        }

    async def _end_session(self, store: str, game_id: str, end_reason: str = "normal") -> None:
        """Record completed session + update totals."""
        if self._db is None:
            return

        key = f"{store}:{game_id}"
        session = self._active.pop(key, None)
        if not session:
            return

        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        # Handle in-flight suspend
        if session["suspended_at"]:
            sleep_duration = (now - session["suspended_at"]).total_seconds()
            session["total_sleep_secs"] += sleep_duration
            session["suspended_at"] = None

        wall_secs = (now - session["started_at"]).total_seconds()
        duration_secs = max(0, int(wall_secs - session["total_sleep_secs"]))

        if duration_secs < _MIN_SESSION_SECONDS:
            logger.debug("[PlaytimeService] Discarding short session (%ds) for %s", duration_secs, session["title"])
            if session["db_row_id"]:
                self._db.execute("DELETE FROM play_sessions WHERE id = ?", (session["db_row_id"],))
                self._db.conn.commit()
            return

        if session["db_row_id"]:
            # Update session
            self._db.execute(
                """UPDATE play_sessions
                   SET ended_at = ?, duration_secs = ?, end_reason = ?,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ?""",
                (now_iso, duration_secs, end_reason, session["db_row_id"]),
            )

            # Update daily stats
            self._update_daily_stats(session["game_db_id"], session["started_at"], now, duration_secs)

            # Refresh materialized totals and streaks
            self._refresh_game_stats(session["game_db_id"])

            self._db.conn.commit()

        logger.info("[PlaytimeService] Session ended: %s (%ds)", session["title"], duration_secs)

        if self._bus:
            self._bus.emit(
                Events.PLAYTIME_UPDATED,
                store=store,
                game_id=game_id,
                duration_secs=duration_secs,
            )

    def _update_daily_stats(self, game_db_id: int, started: datetime, ended: datetime, duration_secs: int) -> None:
        """Split and record duration across day boundaries."""
        if self._db is None:
            return
            
        # Use local time for day boundaries
        local_start = started.astimezone()
        local_end = ended.astimezone()
        
        if local_start.date() == local_end.date():
            splits = [(local_start.strftime("%Y-%m-%d"), duration_secs)]
        else:
            # Complex split logic
            total_wall = (local_end - local_start).total_seconds()
            if total_wall <= 0:
                splits = [(local_start.strftime("%Y-%m-%d"), duration_secs)]
            else:
                ratio = duration_secs / total_wall
                splits = []
                current = local_start
                remaining = duration_secs
                while current.date() < local_end.date():
                    next_midnight = datetime.combine(
                        current.date() + timedelta(days=1), datetime.min.time(), tzinfo=current.tzinfo
                    )
                    wall_on_day = (next_midnight - current).total_seconds()
                    secs_on_day = min(remaining, max(1, int(wall_on_day * ratio)))
                    splits.append((current.strftime("%Y-%m-%d"), secs_on_day))
                    remaining -= secs_on_day
                    current = next_midnight
                if remaining > 0:
                    splits.append((current.strftime("%Y-%m-%d"), remaining))

        for date_str, secs in splits:
            self._db.execute(
                """INSERT INTO daily_stats (game_id, date, total_secs, session_count, longest_session_secs)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(game_id, date) DO UPDATE SET
                       total_secs = total_secs + excluded.total_secs,
                       session_count = session_count + 1,
                       longest_session_secs = MAX(longest_session_secs, excluded.longest_session_secs)""",
                (game_db_id, date_str, secs, secs),
            )

    def _refresh_game_stats(self, game_db_id: int) -> None:
        """Recompute materialized totals and streaks."""
        if self._db is None:
            return

        row = self._db.query_one(
            """SELECT COUNT(*) as total_sessions,
                      COALESCE(SUM(duration_secs), 0) as total_secs,
                      COALESCE(AVG(duration_secs), 0) as avg_session_secs,
                      COALESCE(MAX(duration_secs), 0) as max_session_secs,
                      MIN(started_at) as first_played_at,
                      MAX(started_at) as last_played_at
               FROM play_sessions
               WHERE game_id = ? AND ended_at IS NOT NULL AND duration_secs > 0""",
            (game_db_id,)
        )

        if not row or row["total_sessions"] == 0:
            return

        current_streak, longest_streak = self._compute_streaks(game_db_id)

        self._db.execute(
            """INSERT INTO game_stats
               (game_id, total_secs, total_sessions, avg_session_secs,
                max_session_secs, first_played_at, last_played_at,
                current_streak_days, longest_streak_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(game_id) DO UPDATE SET
                   total_secs = excluded.total_secs,
                   total_sessions = excluded.total_sessions,
                   avg_session_secs = excluded.avg_session_secs,
                   max_session_secs = excluded.max_session_secs,
                   first_played_at = excluded.first_played_at,
                   last_played_at = excluded.last_played_at,
                   current_streak_days = excluded.current_streak_days,
                   longest_streak_days = excluded.longest_streak_days,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (
                game_db_id, row["total_secs"], row["total_sessions"], int(row["avg_session_secs"]),
                row["max_session_secs"], row["first_played_at"], row["last_played_at"],
                current_streak, longest_streak
            )
        )

    def _compute_streaks(self, game_db_id: int) -> tuple[int, int]:
        """Compute consecutive play streaks from daily_stats."""
        if self._db is None:
            return (0, 0)
            
        rows = self._db.query(
            "SELECT DISTINCT date FROM daily_stats WHERE game_id = ? ORDER BY date DESC",
            (game_db_id,)
        )
        if not rows:
            return (0, 0)

        from datetime import date as date_type
        dates = []
        for r in rows:
            try:
                dates.append(datetime.strptime(r["date"], "%Y-%m-%d").date())
            except ValueError:
                continue

        if not dates:
            return (0, 0)

        today = date_type.today()
        current_streak = 0
        expected = today
        for d in dates:
            if d == expected:
                current_streak += 1
                expected -= timedelta(days=1)
            elif d < expected:
                break
        
        if current_streak == 0 and dates[0] == today - timedelta(days=1):
            expected = today - timedelta(days=1)
            for d in dates:
                if d == expected:
                    current_streak += 1
                    expected -= timedelta(days=1)
                elif d < expected:
                    break

        dates_sorted = sorted(set(dates))
        longest_streak = 1
        streak = 1
        for i in range(1, len(dates_sorted)):
            if (dates_sorted[i] - dates_sorted[i-1]) == timedelta(days=1):
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 1
        
        return (current_streak, longest_streak)

