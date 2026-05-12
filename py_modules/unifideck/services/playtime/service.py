"""Playtime tracking service — record game sessions to SQLite.

OP-18a | py_modules/unifideck/services/playtime/service.py

``PlaytimeService`` listens to ``GAME_LAUNCHED`` / ``GAME_STOPPED``
events on the bus and records each session (start + end + duration)
in a SQLite database. The launcher service is the canonical
producer of these events; this service is the canonical consumer
and storage layer for playtime data.

Public API:

* ``get_playtime(store, game_id)`` — aggregate totals for one game;
* ``get_all_playtimes()`` — aggregate totals for every tracked game;
* ``get_session_history(store, game_id, limit)`` — recent session
  list for one game.

The DB connection is opened lazily in ``start`` (off the event loop
because ``sqlite3`` is blocking) and closed in ``stop`` after any
in-progress sessions are flushed.

Very short sessions (< 5 s) are discarded to filter accidental
launches the user immediately cancelled — they would skew the
``session_count`` aggregate otherwise.
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
    """Track and persist game-session durations to SQLite."""

    def __init__(self, bus: EventBus, db_path: str) -> None:
        """Wire the service to the bus and prepare DB state.

        The DB connection itself is not opened here — it would
        block the event loop. ``start()`` opens it.

        Args:
            bus: live event bus on which the service subscribes to
                ``GAME_LAUNCHED`` and ``GAME_STOPPED``.
            db_path: absolute path to the SQLite file (typically
                ``<data_dir>/playtime.db``). Created on first open
                if absent.
        """
        self._bus = bus
        self._db_path = db_path
        self._active: dict[str, float] = {}
        self._db: PlaytimeDB | None = None
        n = auto_wire(self, self._bus)
        logger.info("[PlaytimeService] wired (%d subscriptions)", n)

    async def start(self) -> None:
        """Open the SQLite connection off the event loop.

        ``sqlite3.connect`` plus schema migration are blocking, so
        they run in a thread executor to keep the event loop
        responsive during plugin boot.
        """
        self._db = await asyncio.to_thread(PlaytimeDB, self._db_path)
        logger.info("[PlaytimeService] database ready")

    async def stop(self) -> None:
        """Flush in-progress sessions and close the DB.

        Iterates the in-memory ``_active`` dict and calls
        ``_end_session`` on each, persisting any session that's been
        running long enough to count. Then closes the DB handle.

        This is essential: without this flush, a user closing the
        Steam Deck mid-game would lose the session entirely.
        """
        self._bus.off(Events.GAME_LAUNCHED, self._on_game_launched)
        self._bus.off(Events.GAME_STOPPED, self._on_game_stopped)
        for key in list(self._active.keys()):
            store, game_id = key.split(":", 1)
            await self._end_session(store, game_id)
        if self._db is not None:
            await asyncio.to_thread(self._db.close)

    @subscribe(Events.GAME_LAUNCHED)
    async def _on_game_launched(self, **kwargs) -> None:
        """Record the start time of a new game session.

        Stores the launch timestamp in the in-memory ``_active``
        dict, keyed by ``"<store>:<game_id>"``. The actual DB
        write happens in ``_end_session`` once we know the session's
        duration.
        """
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id:
            self._active[f"{store}:{game_id}"] = time.time()

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs) -> None:
        """Close the open session for the stopped game.

        Delegates to ``_end_session`` which computes the duration,
        filters out sub-5 s sessions, persists to DB, and emits a
        ``playtime_updated`` event for the UI.
        """
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        if store and game_id:
            await self._end_session(store, game_id)

    async def get_playtime(self, store: str, game_id: str) -> dict[str, Any]:
        """Return aggregated playtime for a single (store, game_id).

        Args:
            store: store identifier (e.g. ``"epic"``, ``"gog"``).
            game_id: store-specific game identifier.

        Returns:
            Dict with ``total_seconds``, ``session_count`` and
            ``last_played`` (POSIX timestamp or ``None``). Returns
            zeros if the game has no recorded sessions.
        """
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
        """Return aggregated playtime for every tracked game.

        Useful for "recently played" UI lists. The result is in DB
        order — typically last-played desc thanks to the index, but
        callers shouldn't rely on it and should sort explicitly if
        a specific order matters.

        Returns:
            List of dicts with ``store``, ``game_id``,
            ``total_seconds``, ``session_count``, ``last_played``.
        """
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
        """Return the most recent sessions for one game, newest first.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            limit: maximum number of sessions to return
                (default 20).

        Returns:
            List of dicts ``{start, end, duration}`` where ``start``
            and ``end`` are POSIX timestamps and ``duration`` is the
            session length in seconds.
        """
        assert self._db is not None
        rows = await asyncio.to_thread(
            self._db.fetch_sessions,
            store,
            game_id,
            limit,
        )
        return [{"start": r[0], "end": r[1], "duration": r[2]} for r in rows]

    async def _end_session(self, store: str, game_id: str) -> None:
        """Close an in-progress session and persist it to the DB.

        Pops the launch timestamp from ``_active``, computes the
        duration, and persists only if the session lasted at least
        ``_MIN_SESSION_SECONDS`` (5 s). Shorter sessions are
        discarded: they would skew ``session_count`` aggregates
        when a user clicks "play" then immediately cancels.

        On a successful persist, emits a ``playtime_updated`` event
        for the UI to refresh.

        Args:
            store: store identifier of the ending session.
            game_id: store-specific game id of the ending session.
        """
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
