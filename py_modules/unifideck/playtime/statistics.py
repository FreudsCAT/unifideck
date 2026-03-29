"""Statistics and analytics queries for the activity tracking system."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .database import ActivityDatabase

logger = logging.getLogger("unifideck")


class StatisticsService:
    """Read-only query service for play time statistics and analytics."""

    def __init__(self, db: ActivityDatabase):
        self.db = db

    # ─── Per-Game Queries ────────────────────────────────────────

    def get_game_stats_by_app_id(self, steam_app_id: int) -> Optional[Dict[str, Any]]:
        """Get lifetime stats for a game by its Steam shortcut appId."""
        row = self.db.query_one(
            """SELECT g.id, g.title, g.store, g.steam_app_id,
                      gs.total_secs, gs.total_sessions, gs.avg_session_secs,
                      gs.min_session_secs, gs.max_session_secs,
                      gs.first_played_at, gs.last_played_at,
                      gs.current_streak_days, gs.longest_streak_days
               FROM games g
               LEFT JOIN game_stats gs ON g.id = gs.game_id
               WHERE g.steam_app_id = ?""",
            (steam_app_id,),
        )
        if not row:
            return None
        return {
            "game_id": row["id"],
            "title": row["title"],
            "store": row["store"],
            "steam_app_id": row["steam_app_id"],
            "total_secs": row["total_secs"] or 0,
            "total_sessions": row["total_sessions"] or 0,
            "avg_session_secs": row["avg_session_secs"] or 0,
            "min_session_secs": row["min_session_secs"],
            "max_session_secs": row["max_session_secs"] or 0,
            "first_played_at": row["first_played_at"],
            "last_played_at": row["last_played_at"],
            "current_streak_days": row["current_streak_days"] or 0,
            "longest_streak_days": row["longest_streak_days"] or 0,
        }

    def get_game_sessions(
        self, game_id: int, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get play sessions for a specific game."""
        rows = self.db.query(
            """SELECT ps.id, ps.started_at, ps.ended_at, ps.duration_secs,
                      ps.end_reason, ps.proton_tool, ps.is_manual, ps.session_note,
                      g.title, g.store
               FROM play_sessions ps
               JOIN games g ON ps.game_id = g.id
               WHERE ps.game_id = ? AND ps.ended_at IS NOT NULL
               ORDER BY ps.started_at DESC
               LIMIT ? OFFSET ?""",
            (game_id, limit, offset),
        )
        return [dict(r) for r in rows]

    def get_game_daily_breakdown(
        self, game_id: int, days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get daily play time breakdown for a game."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.db.query(
            """SELECT date, total_secs, session_count, longest_session_secs
               FROM daily_stats
               WHERE game_id = ? AND date >= ?
               ORDER BY date DESC""",
            (game_id, cutoff),
        )
        return [dict(r) for r in rows]

    # ─── Recent Sessions ─────────────────────────────────────────

    def get_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get the most recent play sessions across all games."""
        rows = self.db.query(
            """SELECT ps.id, ps.started_at, ps.ended_at, ps.duration_secs,
                      ps.end_reason, ps.proton_tool, ps.is_manual,
                      g.title, g.store, g.steam_app_id
               FROM play_sessions ps
               JOIN games g ON ps.game_id = g.id
               WHERE ps.ended_at IS NOT NULL AND ps.duration_secs > 0
               ORDER BY ps.started_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    # ─── Daily Totals ────────────────────────────────────────────

    def get_daily_totals(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get aggregated daily play time totals across all games."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.db.query(
            """SELECT date,
                      SUM(total_secs) as total_secs,
                      SUM(session_count) as session_count,
                      COUNT(DISTINCT game_id) as games_played
               FROM daily_stats
               WHERE date >= ?
               GROUP BY date
               ORDER BY date DESC""",
            (cutoff,),
        )
        return [dict(r) for r in rows]

    # ─── Store Summary ───────────────────────────────────────────

    def get_store_summary(self) -> List[Dict[str, Any]]:
        """Get per-store play time summaries."""
        rows = self.db.query(
            """SELECT g.store,
                      COALESCE(SUM(gs.total_secs), 0) as total_secs,
                      COUNT(DISTINCT g.id) as game_count,
                      COALESCE(SUM(gs.total_sessions), 0) as session_count
               FROM games g
               LEFT JOIN game_stats gs ON g.id = gs.game_id
               GROUP BY g.store
               ORDER BY total_secs DESC"""
        )

        results = []
        for row in rows:
            store = row["store"]
            # Find the most played game in this store
            most_played = self.db.query_one(
                """SELECT g.title, gs.total_secs
                   FROM games g
                   JOIN game_stats gs ON g.id = gs.game_id
                   WHERE g.store = ?
                   ORDER BY gs.total_secs DESC
                   LIMIT 1""",
                (store,),
            )
            results.append({
                "store": store,
                "total_secs": row["total_secs"],
                "game_count": row["game_count"],
                "session_count": row["session_count"],
                "most_played_title": most_played["title"] if most_played else None,
                "most_played_secs": most_played["total_secs"] if most_played else 0,
            })
        return results

    # ─── Overall Stats ───────────────────────────────────────────

    def get_overall_stats(self) -> Dict[str, Any]:
        """Get dashboard-level aggregate statistics."""
        row = self.db.query_one(
            """SELECT COALESCE(SUM(total_secs), 0) as total_secs,
                      COALESCE(SUM(total_sessions), 0) as total_sessions,
                      COUNT(*) as total_games_played
               FROM game_stats
               WHERE total_sessions > 0"""
        )

        # Most active hour
        hour_row = self.db.query_one(
            """SELECT CAST(strftime('%H', started_at) AS INTEGER) as hour,
                      COUNT(*) as cnt
               FROM play_sessions
               WHERE ended_at IS NOT NULL
               GROUP BY hour
               ORDER BY cnt DESC
               LIMIT 1"""
        )

        # Most active day of week
        dow_row = self.db.query_one(
            """SELECT CASE CAST(strftime('%w', started_at) AS INTEGER)
                        WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday'
                        WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
                        WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday'
                        WHEN 6 THEN 'Saturday' END as day_name,
                      COUNT(*) as cnt
               FROM play_sessions
               WHERE ended_at IS NOT NULL
               GROUP BY strftime('%w', started_at)
               ORDER BY cnt DESC
               LIMIT 1"""
        )

        # Average daily play time (last 30 days)
        avg_row = self.db.query_one(
            """SELECT COALESCE(AVG(day_total), 0) as avg_daily
               FROM (
                   SELECT SUM(total_secs) as day_total
                   FROM daily_stats
                   WHERE date >= date('now', '-30 days')
                   GROUP BY date
               )"""
        )

        # This week vs last week
        this_week = self.db.query_one(
            """SELECT COALESCE(SUM(total_secs), 0) as total
               FROM daily_stats
               WHERE date >= date('now', 'weekday 1', '-7 days')"""
        )
        last_week = self.db.query_one(
            """SELECT COALESCE(SUM(total_secs), 0) as total
               FROM daily_stats
               WHERE date >= date('now', 'weekday 1', '-14 days')
                 AND date < date('now', 'weekday 1', '-7 days')"""
        )

        return {
            "total_secs": row["total_secs"] if row else 0,
            "total_sessions": row["total_sessions"] if row else 0,
            "total_games_played": row["total_games_played"] if row else 0,
            "most_active_hour": hour_row["hour"] if hour_row else None,
            "most_active_day": dow_row["day_name"] if dow_row else None,
            "average_daily_secs": int(avg_row["avg_daily"]) if avg_row else 0,
            "this_week_secs": this_week["total"] if this_week else 0,
            "last_week_secs": last_week["total"] if last_week else 0,
        }

    # ─── Most Played ─────────────────────────────────────────────

    def get_most_played(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most played games sorted by total time."""
        rows = self.db.query(
            """SELECT g.id as game_id, g.title, g.store, g.steam_app_id,
                      gs.total_secs, gs.total_sessions, gs.last_played_at
               FROM game_stats gs
               JOIN games g ON gs.game_id = g.id
               WHERE gs.total_secs > 0
               ORDER BY gs.total_secs DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    # ─── Hour Distribution ───────────────────────────────────────

    def get_hour_distribution(self) -> List[Dict[str, Any]]:
        """Get activity distribution by hour of day (0-23)."""
        rows = self.db.query(
            """SELECT CAST(strftime('%H', started_at) AS INTEGER) as hour,
                      COUNT(*) as session_count,
                      COALESCE(SUM(duration_secs), 0) as total_secs
               FROM play_sessions
               WHERE ended_at IS NOT NULL AND duration_secs > 0
               GROUP BY hour
               ORDER BY hour"""
        )
        # Fill in missing hours with zeros
        hour_map = {r["hour"]: dict(r) for r in rows}
        return [
            hour_map.get(h, {"hour": h, "session_count": 0, "total_secs": 0})
            for h in range(24)
        ]

    # ─── Day-of-Week Distribution ────────────────────────────────

    def get_day_of_week_distribution(self) -> List[Dict[str, Any]]:
        """Get activity distribution by day of week."""
        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        rows = self.db.query(
            """SELECT CAST(strftime('%w', started_at) AS INTEGER) as dow,
                      COUNT(*) as session_count,
                      COALESCE(SUM(duration_secs), 0) as total_secs
               FROM play_sessions
               WHERE ended_at IS NOT NULL AND duration_secs > 0
               GROUP BY dow
               ORDER BY dow"""
        )
        dow_map = {r["dow"]: dict(r) for r in rows}
        return [
            {
                "day": days[d],
                "day_number": d,
                "session_count": dow_map.get(d, {}).get("session_count", 0),
                "total_secs": dow_map.get(d, {}).get("total_secs", 0),
            }
            for d in range(7)
        ]

    # ─── Weekly Comparison ───────────────────────────────────────

    def get_weekly_comparison(self) -> Dict[str, Any]:
        """Compare this week to last week."""
        this_week = self.db.query_one(
            """SELECT COALESCE(SUM(total_secs), 0) as total_secs,
                      COALESCE(SUM(session_count), 0) as session_count,
                      COUNT(DISTINCT game_id) as games_played
               FROM daily_stats
               WHERE date >= date('now', 'weekday 1', '-7 days')"""
        )
        last_week = self.db.query_one(
            """SELECT COALESCE(SUM(total_secs), 0) as total_secs,
                      COALESCE(SUM(session_count), 0) as session_count,
                      COUNT(DISTINCT game_id) as games_played
               FROM daily_stats
               WHERE date >= date('now', 'weekday 1', '-14 days')
                 AND date < date('now', 'weekday 1', '-7 days')"""
        )

        this_secs = this_week["total_secs"] if this_week else 0
        last_secs = last_week["total_secs"] if last_week else 0
        change_pct = 0
        if last_secs > 0:
            change_pct = round(((this_secs - last_secs) / last_secs) * 100, 1)

        return {
            "this_week": {
                "total_secs": this_secs,
                "session_count": this_week["session_count"] if this_week else 0,
                "games_played": this_week["games_played"] if this_week else 0,
            },
            "last_week": {
                "total_secs": last_secs,
                "session_count": last_week["session_count"] if last_week else 0,
                "games_played": last_week["games_played"] if last_week else 0,
            },
            "change_percent": change_pct,
        }

    # ─── Monthly Totals ──────────────────────────────────────────

    def get_monthly_totals(self, months: int = 12) -> List[Dict[str, Any]]:
        """Get monthly play time aggregates."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=months * 31)).strftime("%Y-%m-%d")
        rows = self.db.query(
            """SELECT strftime('%Y-%m', date) as month,
                      SUM(total_secs) as total_secs,
                      SUM(session_count) as session_count,
                      COUNT(DISTINCT game_id) as games_played
               FROM daily_stats
               WHERE date >= ?
               GROUP BY month
               ORDER BY month DESC""",
            (cutoff,),
        )
        return [dict(r) for r in rows]

    # ─── Game Events History ─────────────────────────────────────

    def get_game_events(
        self, game_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get recent events for a game."""
        rows = self.db.query(
            """SELECT id, event_type, occurred_at, details_json, source
               FROM game_events
               WHERE game_id = ?
               ORDER BY occurred_at DESC
               LIMIT ?""",
            (game_id, limit),
        )
        return [dict(r) for r in rows]

    # ─── Device Events History ───────────────────────────────────

    def get_device_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent device/plugin events."""
        rows = self.db.query(
            """SELECT id, event_type, occurred_at, details_json, steam_user_id
               FROM device_events
               ORDER BY occurred_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]
