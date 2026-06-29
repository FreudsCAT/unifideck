"""services/playtime/service.py — Per-game session tracker.

SQLite-backed playtime tracker. Refactor of legacy playtime/
package. Emits ``PLAYTIME_UPDATED`` after each session.
Persistence lives in ``db.py`` (``PlaytimeDB``); this module
owns event wiring + session lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe

from .db import ActivityDatabase

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Sessions shorter than this are ignored as accidental launches.
_MIN_SESSION_SECONDS = 5

# How often active sessions persist a provisional duration to disk. A crash /
# force-kill (no clean stop) can then be recovered to within this interval on
# the next startup (see ``_reconcile_orphans``). Cheap: one UPDATE per running
# game per tick.
_HEARTBEAT_SECONDS = 60


class PlaytimeService:
    """SQLite-backed playtime tracker wired to the EventBus."""

    def __init__(self, bus: EventBus, db_path: str) -> None:
        """Store refs, init empty ``_active`` map, and auto_wire."""
        self._bus = bus
        self._db_path = db_path
        self._db: ActivityDatabase | None = None
        self._active: dict[str, dict[str, Any]] = {}
        self._heartbeat_task: asyncio.Task[Any] | None = None

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` as if it were a bus
        # method, but ``auto_wire`` is module-level — the
        # call raised ``AttributeError`` and every
        # subscription was lost (caught and silenced upstream).
        auto_wire(self, self._bus)

    async def start(self) -> None:
        """Open the DB, recover any crash-orphaned sessions, start heartbeat."""
        if self._db is None:
            self._db = ActivityDatabase(self._db_path)
            self._db.open()
            # Close sessions left open by a previous crash/force-kill BEFORE
            # any new sessions start, so the recovery can't race a relaunch.
            self._reconcile_orphans()
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        """Cancel heartbeat, flush in-flight sessions, close the DB."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        if self._db is not None:
            # End all active sessions
            keys = list(self._active.keys())
            for key in keys:
                store, game_id = key.split(":", 1)
                await self._end_session(store, game_id, end_reason="plugin_unload")

            self._db.close()
            self._db = None

    @staticmethod
    def _provisional_duration(session: dict[str, Any], now: datetime) -> int:
        """Played seconds so far for ``session`` (wall minus sleep), no mutation.

        Single source of truth for both the heartbeat checkpoint and the final
        ``_end_session`` calc. Folds any in-flight suspend into a LOCAL sleep
        total so it never mutates the live session (the game is still running
        during a heartbeat).
        """
        sleep = session["total_sleep_secs"]
        if session["suspended_at"]:
            sleep += (now - session["suspended_at"]).total_seconds()
        wall_secs = (now - session["started_at"]).total_seconds()
        return max(0, int(wall_secs - sleep))

    async def _heartbeat_loop(self) -> None:
        """Persist a provisional duration for active sessions every tick."""
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_SECONDS)
                try:
                    self._checkpoint_active()
                except Exception:
                    logger.debug(
                        "[PlaytimeService] heartbeat checkpoint failed",
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            pass

    def _checkpoint_active(self) -> None:
        """Write each active session's running duration to disk (crash safety).

        Deliberately leaves ``ended_at`` NULL and ``end_reason`` 'unknown' so
        the row is NOT yet eligible for store sync (``get_unreported_sessions``
        requires ``ended_at``) nor counted in stats (``_refresh_game_stats``
        requires ``ended_at``) — it only records progress. If the process dies
        before a clean stop, ``_reconcile_orphans`` recovers this lower bound.
        """
        if self._db is None or not self._active:
            return
        now = datetime.now(UTC)
        for session in self._active.values():
            row_id = session.get("db_row_id")
            if not row_id:
                continue
            duration = self._provisional_duration(session, now)
            self._db.execute(
                """UPDATE play_sessions
                   SET duration_secs = ?,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ? AND ended_at IS NULL""",
                (duration, row_id),
            )
        self._db._require_conn().commit()

    def _reconcile_orphans(self) -> None:
        """Finalize sessions left open by a crash / force-kill, on startup.

        A clean shutdown flushes active sessions via ``stop()`` → ``_end_session``
        (which sets ``ended_at``), so any row still ``ended_at IS NULL`` here is
        an orphan from a hard restart. We credit the last heartbeat checkpoint
        (``duration_secs`` — a lower bound), label it ``'orphaned'``, and
        recompute that game's stats so the time counts and becomes eligible for
        store sync (``reported_at`` stays NULL → the sync drain picks it up).
        Orphans with no checkpoint (crashed before the first heartbeat) get 0
        duration: closed, but neither counted nor synced.
        """
        if self._db is None:
            return
        rows = self._db.query(
            """SELECT id, game_id, started_at, duration_secs, updated_at
               FROM play_sessions WHERE ended_at IS NULL""",
        )
        if not rows:
            return
        credited: set[int] = set()
        for row in rows:
            duration = int(row["duration_secs"] or 0)
            started = self._parse_iso(row["started_at"])
            # Approximate the end at the last heartbeat (``updated_at``); fall
            # back to ``started + duration`` then to ``started`` so we always
            # close the row even with no checkpoint.
            ended = self._parse_iso(row["updated_at"])
            if ended is None and started is not None:
                ended = started + timedelta(seconds=duration)
            ended_dt = ended or started or datetime.now(UTC)
            ended_iso = ended_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            self._db.execute(
                """UPDATE play_sessions
                   SET ended_at = ?, duration_secs = ?, end_reason = 'orphaned',
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ?""",
                (ended_iso, duration, row["id"]),
            )
            if duration >= _MIN_SESSION_SECONDS and started is not None:
                self._update_daily_stats(row["game_id"], started, ended_dt, duration)
                credited.add(int(row["game_id"]))
        for game_db_id in credited:
            self._refresh_game_stats(game_db_id)
        self._db._require_conn().commit()
        logger.info(
            "[PlaytimeService] Reconciled %d orphaned session(s); credited %d game(s)",
            len(rows), len(credited),
        )

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        """Parse an ISO-8601 (``...Z``) timestamp to aware UTC, or None."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

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

        now = datetime.now(UTC)
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
        self._db._require_conn().commit()
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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
                      gs.current_streak_days, gs.longest_streak_days,
                      sp.store_total_secs
               FROM games g
               JOIN game_stats gs ON g.id = gs.game_id
               LEFT JOIN store_playtime sp ON sp.game_id = g.id
               WHERE g.store = ? AND g.store_game_id = ?""",
            (store, game_id)
        )

        if row:
            return {
                "total_seconds": row["total_secs"],
                # Store's authoritative cross-device total (None until first
                # synced). The frontend prefers this over ``total_seconds``.
                "store_total_secs": row["store_total_secs"],
                "session_count": row["total_sessions"],
                "last_played": row["last_played_at"],
                "current_streak": row["current_streak_days"],
                "longest_streak": row["longest_streak_days"],
                "is_active": is_active,
            }

        return {
            "total_seconds": 0,
            "store_total_secs": None,
            "session_count": 0,
            "last_played": None,
            "current_streak": 0,
            "longest_streak": 0,
            "is_active": is_active,
        }

    async def get_all_playtimes(self) -> list[dict[str, Any]]:
        """Return cumulative playtime for every tracked game.

        Joins ``games`` x ``game_stats`` so each row carries both
        the canonical identity (``store`` + ``store_game_id``) and
        the aggregated stats the frontend needs to render the
        "Most played" list. Returns ``[]`` when the database is
        unavailable so callers don't have to guard the result.

        This method is the bulk equivalent of :meth:`get_playtime`
        — added because the RPC handlers (both the new
        ``rpc/handlers/launch.py`` and the legacy
        ``rpc/mixins/playtime.py``) referenced a ``get_all`` /
        ``get_all_playtimes`` method that didn't exist on the
        service. The RPC therefore always raised
        ``AttributeError`` and the frontend's "playtime stats"
        page could never load.
        """
        if self._db is None:
            return []

        rows = self._db.query(
            """SELECT g.store, g.store_game_id AS game_id, g.title,
                      gs.total_secs, gs.total_sessions, gs.last_played_at,
                      gs.current_streak_days, gs.longest_streak_days,
                      sp.store_total_secs
               FROM games g
               JOIN game_stats gs ON g.id = gs.game_id
               LEFT JOIN store_playtime sp ON sp.game_id = g.id
               ORDER BY gs.total_secs DESC""",
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            key = f"{row['store']}:{row['game_id']}"
            result.append({
                "store": row["store"],
                "game_id": row["game_id"],
                "title": row["title"],
                "total_seconds": row["total_secs"],
                "store_total_secs": row["store_total_secs"],
                "session_count": row["total_sessions"],
                "last_played": row["last_played_at"],
                "current_streak": row["current_streak_days"],
                "longest_streak": row["longest_streak_days"],
                "is_active": key in self._active,
            })
        return result

    async def _end_session(self, store: str, game_id: str, end_reason: str = "normal") -> None:
        """Record completed session + update totals."""
        if self._db is None:
            return

        key = f"{store}:{game_id}"
        session = self._active.pop(key, None)
        if not session:
            return

        now = datetime.now(UTC)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        duration_secs = self._provisional_duration(session, now)

        if duration_secs < _MIN_SESSION_SECONDS:
            logger.debug("[PlaytimeService] Discarding short session (%ds) for %s", duration_secs, session["title"])
            if session["db_row_id"]:
                self._db.execute("DELETE FROM play_sessions WHERE id = ?", (session["db_row_id"],))
                self._db._require_conn().commit()
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

            self._db._require_conn().commit()

        logger.info("[PlaytimeService] Session ended: %s (%ds)", session["title"], duration_secs)

        if self._bus:
            await self._bus.emit(
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
        from datetime import datetime
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
        from datetime import timedelta
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
        from datetime import datetime, timedelta
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
        from datetime import timedelta
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

