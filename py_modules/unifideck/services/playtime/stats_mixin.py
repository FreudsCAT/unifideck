"""services/playtime/stats_mixin.py — daily-stats + streak computation.

Split out of ``service.py`` to keep that module focused on session lifecycle.
``PlaytimeStatsMixin`` is mixed into ``PlaytimeService``; its methods record
the materialized ``daily_stats`` / ``game_stats`` tables and derive play
streaks, all via the shared ``_db`` connection.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .db import ActivityDatabase


class PlaytimeStatsMixin:
    """Daily-stats recording + streak math for :class:`PlaytimeService`."""

    # Provided by ``PlaytimeService.__init__``; declared here for the type
    # checker so the mixin can read/write the shared connection.
    _db: ActivityDatabase | None

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

    @staticmethod
    def _parse_daily_stats_dates(rows: list[Any]) -> list[Any]:
        """Parse ``YYYY-MM-DD`` strings from daily_stats rows into UTC dates.

        The daily_stats schema stores dates as plain strings (see
        ``record_session`` which writes ``datetime.now(timezone.utc)``).
        We re-pin to UTC explicitly so the streak math compares
        apples to apples even on systems with a non-UTC local tz.
        Malformed rows are silently dropped — partial data is fine
        for a UI display, and we already log on write.
        """
        dates: list[Any] = []
        for r in rows:
            try:
                parsed = datetime.strptime(r["date"], "%Y-%m-%d").replace(
                    tzinfo=UTC,
                )
                dates.append(parsed.date())
            except ValueError:
                continue
        return dates

    @staticmethod
    def _walk_consecutive_from(
        dates: list[Any], anchor: date,
    ) -> int:
        """Count consecutive days starting from ``anchor`` going backwards.

        ``dates`` is assumed sorted descending. Returns the number
        of dates matching ``anchor``, ``anchor - 1``, ``anchor - 2``,
        … stopping at the first gap.
        """
        count = 0
        expected = anchor
        for d in dates:
            if d == expected:
                count += 1
                expected -= timedelta(days=1)
            elif d < expected:
                break
        return count

    @classmethod
    def _compute_current_streak(cls, dates: list[Any]) -> int:
        """Compute the current streak ending today (or yesterday).

        Tries today first; if there's no entry for today, falls
        back to yesterday so the streak doesn't drop to 0 the
        moment the date rolls over before the user has played.
        """
        today = datetime.now(UTC).date()

        current = cls._walk_consecutive_from(dates, today)
        if current > 0:
            return current

        # No play today — try yesterday as anchor, but only if
        # the most-recent record actually IS yesterday (otherwise
        # the streak is genuinely broken).
        if dates and dates[0] == today - timedelta(days=1):
            return cls._walk_consecutive_from(dates, today - timedelta(days=1))
        return 0

    @staticmethod
    def _compute_longest_streak(dates: list[Any]) -> int:
        """Compute the longest consecutive run of dates ever seen.

        Operates on a sorted-ascending de-duplicated copy of
        ``dates`` so we can walk forward. The minimum is 1 (any
        single day still counts as a one-day streak).
        """
        dates_sorted = sorted(set(dates))
        longest = 1
        streak = 1
        for i in range(1, len(dates_sorted)):
            if (dates_sorted[i] - dates_sorted[i - 1]) == timedelta(days=1):
                streak += 1
                longest = max(longest, streak)
            else:
                streak = 1
        return longest

    def _compute_streaks(self, game_db_id: int) -> tuple[int, int]:
        """Compute (current, longest) play streaks from daily_stats.

        Both streaks are in whole UTC days. ``current`` is the
        number of consecutive days up to today (or yesterday if
        the user hasn't played today yet); ``longest`` is the
        longest such run anywhere in the history.
        """
        if self._db is None:
            return (0, 0)

        rows = self._db.query(
            "SELECT DISTINCT date FROM daily_stats WHERE game_id = ? ORDER BY date DESC",
            (game_db_id,),
        )
        if not rows:
            return (0, 0)

        dates = self._parse_daily_stats_dates(rows)
        if not dates:
            return (0, 0)

        return (
            self._compute_current_streak(dates),
            self._compute_longest_streak(dates),
        )
