"""event_bus/event_replay.py — Bounded ring buffer of recent events.

# OP-09d | event_bus/event_replay.py | Depends: OP-05

One ``deque(maxlen=N)`` per event type. After each successful
emit, ``record(event, kwargs)`` appends — O(1). ``snapshot()``
returns most-recent entries, newest-first, capped globally so
a frontend reconnect cannot pull megabytes.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..core.types import Events

MAX_SNAPSHOT_ENTRIES = 500

# Per-event default caps. Tuned so progress ticks (noisy) get
# more room, state changes (rare) get less. Values absent from
# this map use ``_FALLBACK_CAP``.
_DEFAULT_CAPS: dict[Events, int] = {
    Events.SYNC_PROGRESS:       50,
    Events.DOWNLOAD_PROGRESS:   50,
    Events.GAME_INSTALLED:      20,
    Events.GAME_UNINSTALLED:    20,
    Events.STORE_AUTH_COMPLETE: 10,
    Events.STORE_LOGOUT:        10,
}

_FALLBACK_CAP = 20


@dataclass
class _RecordedEvent:
    """A single entry in the ring buffer."""
    event: str
    kwargs: dict[str, Any]
    timestamp: float  # monotonic seconds since plugin start

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serialisable dict: event, kwargs, rounded ts."""
        return {
            "event": self.event,
            "kwargs": self.kwargs,
            "ts": round(self.timestamp, 3),
        }


class EventReplayBuffer:
    """Per-type ring buffer of recent events."""

    def __init__(
        self,
        *,
        fallback_cap: int = _FALLBACK_CAP,
        caps: dict[Events, int] | None = None,
    ) -> None:
        """Init caps dict (defaults + caller overrides) and empty
        ``{event_str: deque}`` map; deques are created lazily on
        first ``record()`` for each type.
        """
        self._caps: dict[str, int] = {}
        # Merge default caps
        for evt, cap in _DEFAULT_CAPS.items():
            self._caps[evt.value if hasattr(evt, "value") else str(evt)] = cap
        # Apply caller overrides
        if caps:
            for evt, cap in caps.items():
                self._caps[evt.value if hasattr(evt, "value") else str(evt)] = cap
        self._fallback_cap = fallback_cap
        self._buffers: dict[str, deque[_RecordedEvent]] = {}
        self._start_time = time.monotonic()

    def record(
        self,
        event: Events | str,
        kwargs: dict[str, Any],
    ) -> None:
        """Append one event to its per-type ring buffer.
        ``kwargs`` is stored by reference; callers must not mutate
        after recording (EventBus already treats emitted kwargs as
        immutable so this is safe in practice).
        """
        key = event.value if hasattr(event, "value") else str(event)
        buf = self._buffers.get(key)
        if buf is None:
            cap = self._caps.get(key, self._fallback_cap)
            buf = deque(maxlen=cap)
            self._buffers[key] = buf
        buf.appendleft(_RecordedEvent(
            event=key,
            kwargs=kwargs,
            timestamp=time.monotonic() - self._start_time,
        ))

    def snapshot(
        self, events: Iterable[Events | str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent events as dicts, newest-first, globally capped
        to ``MAX_SNAPSHOT_ENTRIES``. Pass ``events`` to filter by type.
        """
        if events is not None:
            keys = {e.value if hasattr(e, "value") else str(e) for e in events}
        else:
            keys = None

        all_entries: list[_RecordedEvent] = []
        for key, buf in self._buffers.items():
            if keys is not None and key not in keys:
                continue
            all_entries.extend(buf)

        # Sort newest-first by timestamp
        all_entries.sort(key=lambda e: e.timestamp, reverse=True)

        # Cap globally
        return [e.to_dict() for e in all_entries[:MAX_SNAPSHOT_ENTRIES]]

    def clear(self, event: Events | str | None = None) -> None:
        """Clear one event type's buffer, or all if None."""
        if event is None:
            self._buffers.clear()
        else:
            key = event.value if hasattr(event, "value") else str(event)
            buf = self._buffers.get(key)
            if buf is not None:
                buf.clear()
