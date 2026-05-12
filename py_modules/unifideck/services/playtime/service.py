"""Playtime tracking service.

OP-18a | py_modules/unifideck/services/playtime/service.py

``PlaytimeService`` exposes :

* ``record_launch(game)`` — open a session entry with the current ts;
* ``record_exit(game)``   — close the open session (compute duration);
* ``total_for(game_id)``  — sum of all session durations;
* ``recent(n)`` — N most recently played games;
* ``history(game_id)`` — raw session list for a game.

All times are persisted in a SQLite DB (``db.py``, OP-18b) so the
data survives plugin restarts. The launcher service feeds this
service the launch/exit events.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Any
from ...core.types import Events
from ...event_bus.event_bus import EventBus
from ...event_bus.event_bus_devex import auto_wire, subscribe
from .db import PlaytimeDB

logger = logging.getLogger(__name__)
_MIN_SESSION_SECONDS = 5


class PlaytimeService:
    """Playtime service."""

    def __init__(self, bus: EventBus, db_path: str) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._db_path = db_path
        self._active: dict[str, float] = {}
        self._db: PlaytimeDB | None = None
        n = auto_wire(self, self._bus)
        logger.info("[PlaytimeService] wired (%d subscriptions)", n)

    async def start(self) -> None:
        """Start."""
        self._db = await asyncio.to_thread(PlaytimeDB, self._db_path)
        logger.info("[PlaytimeService] database ready")

    async def stop(self) -> None:
        """Stop."""
        self._bus.off(Events.GAME_LAUNCHED, self._on_game_launched)
        self._bus.off(Events.GAME_STOPPED, self._on_game_stopped)
        for key in list(self._active.keys()):
            store, game_id = key.split(":", 1)
            await self._end_session(store, game_id)
        if self._db is not None:
            await asyncio.to_thread(self._db.close)

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs) -> None:
        """On game launched."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id:
            self._active[f"{store}:{game_id}"] = time.time()

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs) -> None:
        """On game stopped."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id:
            await self._end_session(store, game_id)

    async def get_playtime(self, store: str, game_id: str) -> dict[str, Any]:
        """Get playtime."""
        assert self._db is not None
        row = await asyncio.to_thread(
            self._db.fetch_total,
            store,
            game_id,
        )
        if row is None:
            return {
                "total_seconds": 0,
                "session_count": 0,
                "last_played": None,
            }
        return {
            "total_seconds": row[0],
            "session_count": row[1],
            "last_played": row[2],
        }

    async def get_all_playtimes(self) -> list[dict[str, Any]]:
        """Get all playtimes."""
        assert self._db is not None
        rows = await asyncio.to_thread(self._db.fetch_all_totals)
        return [
            {
                "store": r[0],
                "game_id": r[1],
                "total_seconds": r[2],
                "session_count": r[3],
                "last_played": r[4],
            }
            for r in rows
        ]

    async def get_session_history(
        self,
        store: str,
        game_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Get session history."""
        assert self._db is not None
        rows = await asyncio.to_thread(
            self._db.fetch_sessions,
            store,
            game_id,
            limit,
        )
        return [{"start": r[0], "end": r[1], "duration": r[2]} for r in rows]

    async def _end_session(self, store: str, game_id: str) -> None:
        """End session."""
        key = f"{store}:{game_id}"
        start_ts = self._active.pop(key, None)
        if start_ts is None:
            return
        end_ts = time.time()
        duration = int(end_ts - start_ts)
        if duration < _MIN_SESSION_SECONDS:
            return
        assert self._db is not None
        await asyncio.to_thread(
            self._db.insert_session,
            store,
            game_id,
            start_ts,
            end_ts,
            duration,
        )
        await self._bus.emit(
            "playtime_updated",
            store=store,
            game_id=game_id,
            duration_seconds=duration,
        )
