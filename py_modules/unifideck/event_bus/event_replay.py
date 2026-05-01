"""event_bus/event_replay.py — Bounded ring buffer of recent events.

Design:
  - One `deque(maxlen=N)` per event type (defaults per type).
  - `record(event, kwargs)` is called by the EventBus after a
    successful emit. Cheap — O(1) append.
  - `snapshot(events, limit)` returns the most recent events of
    the requested types, newest-first, with a hard cap on result
    size to prevent a reconnect from transferring a MB of data.
  - **Security-aware**: the default per-type cap is low (50 for
    progress, 20 for state), and the global cap is 500. A
    caller can't trigger a memory leak.
  - **Never stores secrets**: callers are responsible for not
    passing OAuth tokens or passwords as kwargs. The buffer just
    records whatever EventBus receives.

Per-event defaults:
  - SYNC_PROGRESS / DOWNLOAD_PROGRESS → 50 entries (progress ticks)
  - GAME_INSTALLED / GAME_UNINSTALLED → 20 entries (state)
  - STORE_AUTH_COMPLETE / STORE_LOGOUT → 10 entries
  - Everything else → 20 entries

"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..core.types import Events

# Global hard cap — a single snapshot() call can never return more
# than this many events regardless of per-type limits. Prevents a
# malformed RPC request from locking up the loop.
MAX_SNAPSHOT_ENTRIES = 500

# Per-event defaults. Values not in this map use the fallback.
_DEFAULT_CAPS: dict[Events, int] = {
    Events.SYNC_PROGRESS:     50,
    Events.DOWNLOAD_PROGRESS: 50,
    Events.GAME_INSTALLED:    20,
    Events.GAME_UNINSTALLED:  20,
    Events.STORE_AUTH_COMPLETE: 10,
    Events.STORE_LOGOUT:      10,
}

_FALLBACK_CAP = 20


@dataclass
class _RecordedEvent:
    """A single entry in the ring buffer."""

    event: str
    kwargs: dict[str, Any]
    timestamp: float  # monotonic seconds since plugin start

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "kwargs": self.kwargs,
            "timestamp": round(self.timestamp, 3),
        }


class EventReplayBuffer:
    """Ring buffer of recent events, one deque per type.

    Usage:
        replay = EventReplayBuffer()
        # Inside EventBus.emit, after dispatching:
        replay.record(Events.SYNC_PROGRESS, {"store": "epic", "pct": 42})
        # Frontend reconnect:
        snap = replay.snapshot([Events.SYNC_PROGRESS], limit=100)
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        *,
        fallback_cap: int = _FALLBACK_CAP,
        caps: dict[Events, int] | None = None,
    ) -> None:
        self._fallback_cap = fallback_cap
        self._caps = dict(_DEFAULT_CAPS)
        if caps:
            self._caps.update(caps)
        self._buffers: dict[str, deque[_RecordedEvent]] = {}

    # ── Recording ───────────────────────────────────────────────

    def record(
        self,
        event: Events | str,
        kwargs: dict[str, Any],
    ) -> None:
        """Append an event to the appropriate ring buffer.

        The `kwargs` dict is stored by reference — callers should
        not mutate it after calling `record()`. EventBus already
        treats kwargs as immutable once emitted, so this is safe
        in practice.
        """
        event_str = event.value if isinstance(event, Events) else str(event)
        buf = self._buffers.get(event_str)
        if buf is None:
            cap = self._resolve_cap(event)
            buf = deque(maxlen=cap)
            self._buffers[event_str] = buf
        buf.append(
            _RecordedEvent(
                event=event_str,
                kwargs=kwargs,
                timestamp=time.monotonic(),
            ),
        )

    # ── Retrieval ───────────────────────────────────────────────

    def snapshot(
        self,
        events: Iterable[Events | str] | None = None,
        limit: int = MAX_SNAPSHOT_ENTRIES,
    ) -> list[dict[str, Any]]:
        """Return recent events matching the filter, newest first.

        Args:
          events: iterable of event types to include. None means
            "all recorded types".
          limit: hard cap on result size. Clamped to
            MAX_SNAPSHOT_ENTRIES to prevent unbounded responses.

        Returns a list of dicts (not _RecordedEvent instances) so
        the result is directly JSON-serializable for the RPC layer.

        """
        limit = min(limit, MAX_SNAPSHOT_ENTRIES)
        wanted = self._resolve_wanted_set(events)
        gathered: list[_RecordedEvent] = []
        for event_str, buf in self._buffers.items():
            if wanted is not None and event_str not in wanted:
                continue
            gathered.extend(buf)
        gathered.sort(key=lambda r: r.timestamp, reverse=True)
        return [r.to_dict() for r in gathered[:limit]]

    def size(self, event: Events | str | None = None) -> int:
        """Return the number of stored entries (total or per event)."""
        if event is None:
            return sum(len(b) for b in self._buffers.values())
        event_str = event.value if isinstance(event, Events) else str(event)
        buf = self._buffers.get(event_str)
        return len(buf) if buf is not None else 0

    def clear(self) -> None:
        """Drop all recorded events. Useful in tests."""
        self._buffers.clear()

    # ── Private helpers ─────────────────────────────────────────

    def _resolve_cap(self, event: Events | str) -> int:
        """Return the per-type cap for a given event."""
        if isinstance(event, Events):
            return self._caps.get(event, self._fallback_cap)
        try:
            resolved = Events(event)
            return self._caps.get(resolved, self._fallback_cap)
        except ValueError:
            return self._fallback_cap

    @staticmethod
    def _resolve_wanted_set(
        events: Iterable[Events | str] | None,
    ) -> set | None:
        """Normalize the filter iterable to a set of string keys."""
        if events is None:
            return None
        out: set = set()
        for e in events:
            out.add(e.value if isinstance(e, Events) else str(e))
        return out
