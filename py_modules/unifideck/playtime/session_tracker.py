"""Play session tracking with suspend/resume and day-boundary splitting."""

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .database import ActivityDatabase
from .event_recorder import EventRecorder
from .models import ActiveSession, EndReason, GameEventType

logger = logging.getLogger("unifideck")

# Sessions shorter than this are discarded (accidental launch, launcher flicker)
MIN_SESSION_SECS = 5


class SessionTracker:
    """Tracks active play sessions in memory with SQLite persistence.

    In-memory state: Dict[steam_app_id, ActiveSession]
    On start: INSERT into play_sessions with ended_at=NULL
    On end: UPDATE with ended_at, duration_secs; update daily_stats + game_stats
    On suspend: Record suspend timestamp, pause clock
    On resume: Calculate sleep gap, add to total_sleep_secs
    """

    def __init__(self, db: ActivityDatabase, event_recorder: EventRecorder):
        self.db = db
        self.event_recorder = event_recorder
        self.active_sessions: Dict[int, ActiveSession] = {}

    def start_session(
        self,
        steam_app_id: int,
        store: str,
        store_game_id: str,
        title: str,
        steam_user_id: Optional[str] = None,
        proton_tool: Optional[str] = None,
    ) -> Optional[int]:
        """Start tracking a new play session.

        Args:
            steam_app_id: Non-Steam shortcut appId.
            store: Store name.
            store_game_id: Store-specific game ID.
            title: Game title.
            steam_user_id: Current Steam account ID.
            proton_tool: Proton/compatibility tool in use.

        Returns:
            The play_sessions.id or None on failure.
        """
        # Don't double-start
        if steam_app_id in self.active_sessions:
            logger.warning(
                f"[PLAYTIME] Session already active for appId {steam_app_id}, ignoring start"
            )
            return self.active_sessions[steam_app_id].db_row_id

        try:
            # Get or create game in registry
            game_db_id = self.db.get_or_create_game(
                store=store,
                store_game_id=store_game_id,
                title=title,
                steam_app_id=steam_app_id,
            )

            now = datetime.now(timezone.utc)
            now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            # Insert session with ended_at=NULL (active)
            cursor = self.db.execute(
                """INSERT INTO play_sessions
                   (game_id, steam_user_id, started_at, end_reason, proton_tool)
                   VALUES (?, ?, ?, ?, ?)""",
                (game_db_id, steam_user_id, now_iso, EndReason.UNKNOWN.value, proton_tool),
            )
            row_id = cursor.lastrowid

            # Check if this is the first launch ever for this game
            existing_sessions = self.db.query_one(
                "SELECT COUNT(*) as cnt FROM play_sessions WHERE game_id = ? AND id != ?",
                (game_db_id, row_id),
            )
            if existing_sessions and existing_sessions["cnt"] == 0:
                self.event_recorder.record_game_event(
                    game_db_id,
                    GameEventType.FIRST_LAUNCH,
                    details={"proton_tool": proton_tool},
                )

            # Store in-memory
            session = ActiveSession(
                game_db_id=game_db_id,
                store=store,
                store_game_id=store_game_id,
                steam_app_id=steam_app_id,
                title=title,
                started_at=now,
                proton_tool=proton_tool,
                db_row_id=row_id,
            )
            self.active_sessions[steam_app_id] = session

            logger.info(
                f"[PLAYTIME] Session started: {title} ({store}:{store_game_id}, "
                f"appId={steam_app_id}, session_id={row_id})"
            )
            return row_id

        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to start session for appId {steam_app_id}: {e}")
            return None

    def end_session(
        self,
        steam_app_id: int,
        end_reason: EndReason = EndReason.NORMAL,
    ) -> Optional[int]:
        """End an active play session.

        Computes duration (minus sleep gaps), updates daily_stats and game_stats.
        Discards sessions shorter than MIN_SESSION_SECS.

        Args:
            steam_app_id: Non-Steam shortcut appId.
            end_reason: Why the session ended.

        Returns:
            Duration in seconds, or None if no active session.
        """
        session = self.active_sessions.pop(steam_app_id, None)
        if not session:
            logger.debug(f"[PLAYTIME] No active session for appId {steam_app_id}")
            return None

        try:
            now = datetime.now(timezone.utc)
            now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            # If currently suspended, add remaining sleep gap
            if session.suspended_at:
                sleep_duration = (now - session.suspended_at).total_seconds()
                session.total_sleep_secs += sleep_duration
                session.suspended_at = None

            # Compute actual play duration (wall time minus sleep)
            wall_secs = (now - session.started_at).total_seconds()
            duration_secs = max(0, int(wall_secs - session.total_sleep_secs))

            # Discard very short sessions (accidental launches)
            if duration_secs < MIN_SESSION_SECS:
                logger.debug(
                    f"[PLAYTIME] Discarding short session ({duration_secs}s) "
                    f"for {session.title}"
                )
                # Delete the row we inserted on start
                if session.db_row_id:
                    self.db.execute(
                        "DELETE FROM play_sessions WHERE id = ?",
                        (session.db_row_id,),
                    )
                return None

            # Update the session row
            if session.db_row_id:
                self.db.execute(
                    """UPDATE play_sessions
                       SET ended_at = ?, duration_secs = ?, end_reason = ?,
                           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE id = ?""",
                    (now_iso, duration_secs, end_reason.value, session.db_row_id),
                )

            # Update daily_stats (split across midnight if needed)
            self._update_daily_stats(session.game_db_id, session.started_at, now, duration_secs)

            # Refresh game_stats
            self._refresh_game_stats(session.game_db_id)

            logger.info(
                f"[PLAYTIME] Session ended: {session.title} "
                f"({duration_secs}s, reason={end_reason.value})"
            )
            return duration_secs

        except Exception as e:
            logger.error(
                f"[PLAYTIME] Failed to end session for {session.title}: {e}",
                exc_info=True,
            )
            return None

    def suspend_all_sessions(self):
        """Pause the clock for all active sessions (device entering sleep).

        Called from frontend via SteamClient.System.RegisterForOnSuspendRequest.
        """
        now = datetime.now(timezone.utc)
        count = 0
        for session in self.active_sessions.values():
            if session.suspended_at is None:
                session.suspended_at = now
                count += 1
        if count > 0:
            logger.info(f"[PLAYTIME] Suspended {count} active session(s)")
        return count

    def resume_all_sessions(self):
        """Resume the clock for all suspended sessions (device waking up).

        Called from frontend via SteamClient.System.RegisterForOnResumeFromSuspend.
        """
        now = datetime.now(timezone.utc)
        count = 0
        for session in self.active_sessions.values():
            if session.suspended_at is not None:
                sleep_duration = (now - session.suspended_at).total_seconds()
                session.total_sleep_secs += sleep_duration
                session.suspended_at = None
                count += 1
                logger.debug(
                    f"[PLAYTIME] Resumed {session.title}: slept {sleep_duration:.0f}s"
                )
        if count > 0:
            logger.info(f"[PLAYTIME] Resumed {count} session(s)")
        return count

    def end_all_sessions(self, end_reason: EndReason) -> int:
        """End all active sessions (plugin unload, shutdown, etc.).

        Args:
            end_reason: Why all sessions are ending.

        Returns:
            Number of sessions closed.
        """
        app_ids = list(self.active_sessions.keys())
        closed = 0
        for app_id in app_ids:
            result = self.end_session(app_id, end_reason)
            if result is not None:
                closed += 1
        return closed

    def recover_orphaned_sessions(self) -> int:
        """Close any sessions left with ended_at=NULL from crashes/power loss.

        Called on plugin startup. Sets duration=0 (unknown) and end_reason='crash'.

        Returns:
            Number of orphaned sessions recovered.
        """
        try:
            orphans = self.db.query(
                "SELECT id, game_id, started_at FROM play_sessions WHERE ended_at IS NULL"
            )
            if not orphans:
                return 0

            for orphan in orphans:
                self.db.execute(
                    """UPDATE play_sessions
                       SET ended_at = started_at, duration_secs = 0,
                           end_reason = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE id = ?""",
                    (EndReason.CRASH.value, orphan["id"]),
                )
                self.event_recorder.record_activity(
                    category="recovery",
                    action="orphaned_session_closed",
                    details={
                        "session_id": orphan["id"],
                        "game_id": orphan["game_id"],
                        "started_at": orphan["started_at"],
                    },
                )

            logger.info(f"[PLAYTIME] Recovered {len(orphans)} orphaned session(s)")
            return len(orphans)

        except Exception as e:
            logger.error(f"[PLAYTIME] Orphan recovery failed: {e}")
            return 0

    def get_active_session_count(self) -> int:
        """Return the number of currently active sessions."""
        return len(self.active_sessions)

    def get_active_session_info(self, steam_app_id: int) -> Optional[Dict]:
        """Get info about an active session for UI display."""
        session = self.active_sessions.get(steam_app_id)
        if not session:
            return None
        now = datetime.now(timezone.utc)
        wall_secs = (now - session.started_at).total_seconds()
        play_secs = max(0, wall_secs - session.total_sleep_secs)
        if session.suspended_at:
            # Currently suspended — don't count time since suspend
            play_secs -= (now - session.suspended_at).total_seconds()
            play_secs = max(0, play_secs)
        return {
            "title": session.title,
            "store": session.store,
            "started_at": session.started_at.isoformat(),
            "play_secs": int(play_secs),
            "is_suspended": session.suspended_at is not None,
        }

    # ─── Internal helpers ───────────────────────────────────────────

    def _split_session_into_days(
        self, started: datetime, ended: datetime, duration_secs: int
    ) -> List[Tuple[str, int]]:
        """Split a session's duration across midnight boundaries for daily_stats.

        The play_sessions row stays as one record; only daily_stats gets split.

        Args:
            started: Session start (UTC).
            ended: Session end (UTC).
            duration_secs: Total play duration (excluding sleep).

        Returns:
            List of (date_str 'YYYY-MM-DD', seconds_on_that_day).
        """
        # Use local time for day boundaries (user's perspective)
        local_start = started.astimezone()
        local_end = ended.astimezone()

        if local_start.date() == local_end.date():
            return [(local_start.strftime("%Y-%m-%d"), duration_secs)]

        results = []
        total_wall = (local_end - local_start).total_seconds()
        if total_wall <= 0:
            return [(local_start.strftime("%Y-%m-%d"), duration_secs)]

        # Ratio of play time to wall time (accounts for sleep gaps)
        ratio = duration_secs / total_wall

        current = local_start
        remaining = duration_secs
        while current.date() < local_end.date():
            next_midnight = datetime.combine(
                current.date() + timedelta(days=1), time.min, tzinfo=current.tzinfo
            )
            wall_on_day = (next_midnight - current).total_seconds()
            secs_on_day = min(remaining, max(1, int(wall_on_day * ratio)))
            results.append((current.strftime("%Y-%m-%d"), secs_on_day))
            remaining -= secs_on_day
            current = next_midnight

        # Last partial day
        if remaining > 0:
            results.append((current.strftime("%Y-%m-%d"), remaining))

        return results

    def _update_daily_stats(
        self, game_id: int, started: datetime, ended: datetime, duration_secs: int
    ):
        """Update daily_stats with the completed session."""
        try:
            day_splits = self._split_session_into_days(started, ended, duration_secs)
            for date_str, secs in day_splits:
                self.db.execute(
                    """INSERT INTO daily_stats (game_id, date, total_secs, session_count, longest_session_secs)
                       VALUES (?, ?, ?, 1, ?)
                       ON CONFLICT(game_id, date) DO UPDATE SET
                           total_secs = total_secs + excluded.total_secs,
                           session_count = session_count + 1,
                           longest_session_secs = MAX(longest_session_secs, excluded.longest_session_secs)""",
                    (game_id, date_str, secs, secs),
                )
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to update daily_stats: {e}")

    def _refresh_game_stats(self, game_id: int):
        """Recompute the materialized game_stats row for a game."""
        try:
            row = self.db.query_one(
                """SELECT
                       COUNT(*) as total_sessions,
                       COALESCE(SUM(duration_secs), 0) as total_secs,
                       COALESCE(AVG(duration_secs), 0) as avg_session_secs,
                       MIN(duration_secs) as min_session_secs,
                       COALESCE(MAX(duration_secs), 0) as max_session_secs,
                       MIN(started_at) as first_played_at,
                       MAX(started_at) as last_played_at
                   FROM play_sessions
                   WHERE game_id = ? AND ended_at IS NOT NULL AND duration_secs > 0""",
                (game_id,),
            )

            if not row or row["total_sessions"] == 0:
                return

            # Compute streaks from daily_stats
            current_streak, longest_streak = self._compute_streaks(game_id)

            self.db.execute(
                """INSERT INTO game_stats
                   (game_id, total_secs, total_sessions, avg_session_secs,
                    min_session_secs, max_session_secs, first_played_at,
                    last_played_at, current_streak_days, longest_streak_days)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(game_id) DO UPDATE SET
                       total_secs = excluded.total_secs,
                       total_sessions = excluded.total_sessions,
                       avg_session_secs = excluded.avg_session_secs,
                       min_session_secs = excluded.min_session_secs,
                       max_session_secs = excluded.max_session_secs,
                       first_played_at = excluded.first_played_at,
                       last_played_at = excluded.last_played_at,
                       current_streak_days = excluded.current_streak_days,
                       longest_streak_days = excluded.longest_streak_days,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (
                    game_id,
                    row["total_secs"],
                    row["total_sessions"],
                    int(row["avg_session_secs"]),
                    row["min_session_secs"],
                    row["max_session_secs"],
                    row["first_played_at"],
                    row["last_played_at"],
                    current_streak,
                    longest_streak,
                ),
            )
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to refresh game_stats for game_id={game_id}: {e}")

    def _compute_streaks(self, game_id: int) -> Tuple[int, int]:
        """Compute current and longest consecutive-day play streaks.

        Args:
            game_id: The game to compute streaks for.

        Returns:
            (current_streak_days, longest_streak_days)
        """
        try:
            rows = self.db.query(
                "SELECT DISTINCT date FROM daily_stats WHERE game_id = ? ORDER BY date DESC",
                (game_id,),
            )
            if not rows:
                return (0, 0)

            dates = []
            for r in rows:
                try:
                    dates.append(datetime.strptime(r["date"], "%Y-%m-%d").date())
                except ValueError:
                    continue

            if not dates:
                return (0, 0)

            # Current streak: count consecutive days from today backwards
            from datetime import date as date_type
            today = date_type.today()
            current_streak = 0
            expected = today
            for d in dates:
                if d == expected:
                    current_streak += 1
                    expected -= timedelta(days=1)
                elif d < expected:
                    break

            # If didn't play today, check if played yesterday for current streak
            if current_streak == 0 and dates and dates[0] == today - timedelta(days=1):
                expected = today - timedelta(days=1)
                for d in dates:
                    if d == expected:
                        current_streak += 1
                        expected -= timedelta(days=1)
                    elif d < expected:
                        break

            # Longest streak: scan all dates
            dates_sorted = sorted(set(dates))
            longest_streak = 1
            streak = 1
            for i in range(1, len(dates_sorted)):
                if dates_sorted[i] - dates_sorted[i - 1] == timedelta(days=1):
                    streak += 1
                    longest_streak = max(longest_streak, streak)
                else:
                    streak = 1

            return (current_streak, longest_streak)

        except Exception as e:
            logger.error(f"[PLAYTIME] Streak computation failed: {e}")
            return (0, 0)
