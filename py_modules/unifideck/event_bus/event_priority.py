"""event_bus/event_priority.py — Priority classification for events.

# OP-09b | event_bus/event_priority.py | Depends: OP-05

Three-level dispatch priority so critical UI events (GAME_LAUNCHED)
bypass thousands of pending background ticks (SYNC_PROGRESS).
Priority is a property of the event TYPE, not the call site.
"""
from __future__ import annotations

from enum import IntEnum

from ..core.types import Events


class EventPriority(IntEnum):
    """Three-level dispatch priority. Lower int = higher priority
    (matches ``asyncio.PriorityQueue`` smallest-first ordering).
    """
    CRITICAL = 0
    NORMAL = 1
    BACKGROUND = 2


# Default priority per event. Every Events member MUST appear here —
# test suite enforces full coverage. Plugin/game lifecycle → CRITICAL.
# User-triggered ops (auth, sync start/done) → NORMAL. Progress +
# errors → BACKGROUND (idempotent, safe to drop under pressure).
_DEFAULT_PRIORITY: dict[Events, EventPriority] = {
    # Plugin lifecycle
    Events.PLUGIN_LOADED:           EventPriority.CRITICAL,
    Events.PLUGIN_UNLOADING:        EventPriority.CRITICAL,
    # Game lifecycle
    Events.GAME_LAUNCHED:           EventPriority.CRITICAL,
    Events.GAME_STOPPED:            EventPriority.CRITICAL,
    Events.GAME_INSTALLED:          EventPriority.NORMAL,
    Events.GAME_UNINSTALLED:        EventPriority.NORMAL,
    Events.GAME_UPDATE_AVAILABLE:   EventPriority.BACKGROUND,
    # Sync lifecycle
    Events.SYNC_STARTED:            EventPriority.NORMAL,
    Events.SYNC_COMPLETE:           EventPriority.NORMAL,
    Events.SYNC_CANCELLED:          EventPriority.NORMAL,
    Events.SYNC_FAILED:             EventPriority.NORMAL,
    Events.SYNC_PROGRESS:           EventPriority.BACKGROUND,
    # Store auth lifecycle
    Events.STORE_AUTH_STARTED:      EventPriority.NORMAL,
    Events.STORE_AUTH_COMPLETE:     EventPriority.NORMAL,
    Events.STORE_AUTH_FAILED:       EventPriority.NORMAL,
    Events.STORE_LOGOUT:            EventPriority.NORMAL,
    # Download lifecycle
    Events.DOWNLOAD_QUEUED:         EventPriority.NORMAL,
    Events.DOWNLOAD_STARTED:        EventPriority.NORMAL,
    Events.DOWNLOAD_COMPLETE:       EventPriority.NORMAL,
    Events.DOWNLOAD_CANCELLED:      EventPriority.NORMAL,
    Events.DOWNLOAD_FAILED:         EventPriority.NORMAL,
    Events.DOWNLOAD_PROGRESS:       EventPriority.BACKGROUND,
    # Generic
    Events.STORE_ERROR:             EventPriority.BACKGROUND,
}

# Events whose payloads are idempotent enough to be coalesced in the
# dispatch queue. Value = kwarg name that distinguishes otherwise
# identical events. When a second event with the same (type, key)
# arrives before the first is dispatched, the older one is replaced.
COALESCE_KEY: dict[Events, str] = {
    Events.SYNC_PROGRESS:      "store",
    Events.DOWNLOAD_PROGRESS:  "download_id",
}


def get_priority(event: Events | str) -> EventPriority:
    """Return default priority for an event type.
    Accepts Events enum or raw string (legacy). Unknown events fall
    back to NORMAL — never BACKGROUND — so a forgotten classification
    can't be silently dropped.
    """
    raise NotImplementedError("OP-09b: lookup _DEFAULT_PRIORITY, fallback NORMAL")


def get_coalesce_key(event: Events | str) -> str:
    """Return the coalesce-key kwarg name, or '' if no coalescing.
    Empty = each emission kept as a distinct queue entry. Non-empty
    = replace any pending event of the same (type, kwargs[key]).
    """
    raise NotImplementedError("OP-09b: lookup COALESCE_KEY, return '' if absent")
