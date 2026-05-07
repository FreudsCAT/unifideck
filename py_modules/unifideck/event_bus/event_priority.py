"""event_bus/event_priority.py — Priority classification for EventBus events.

The EventBus is the central nervous system of the plugin. During a
mass library sync (5 stores × hundreds of games), it can process
thousands of events per second. On the Steam Deck — where the main
thread already shares CPU with gamescope and SteamUI — an unbounded
FIFO queue produces visible UI micro-freezes as background handlers
(artwork fetch, metadata scraping) delay the user-facing events
(game launched, CSS injection).

This module classifies every `Events.*` value into one of three
priority levels so the EventBus can dispatch critical UI events
ahead of background work:

  CRITICAL   — user-facing, must dispatch within a few ms. Never
               dropped, never coalesced. Examples: GAME_LAUNCHED,
               GAME_STOPPED, PLUGIN_UNLOADING.
  NORMAL     — user-triggered operations. Coalescing allowed for
               progress-type events. Examples: SYNC_STARTED,
               DOWNLOAD_QUEUED, STORE_AUTH_COMPLETE.
  BACKGROUND — housekeeping and telemetry. Subject to coalescing
               and drop on queue saturation. Examples: SYNC_PROGRESS,
               DOWNLOAD_PROGRESS, metrics fan-out.

Design principles:
  - Priority is a property of the *event type*, not the call site.
    Emitters never guess priority; they just emit the event and
    trust the central classification.
  - Callers can override via `emit(event, priority=...)` for
    unusual situations (e.g., a SYNC_PROGRESS that happens to be
    the final 100% tick and should NOT be coalesced).
  - Unknown event types (string keys not in the Events enum) fall
    back to NORMAL — fails safe, never BACKGROUND.

"""
from __future__ import annotations

from enum import IntEnum

from ..core.types import Events


class EventPriority(IntEnum):
    """Three-level dispatch priority.

    Lower integer = higher priority. IntEnum is used so the values
    compare naturally with `<` in asyncio.PriorityQueue, which
    orders smallest-first.
    """

    CRITICAL = 0
    NORMAL = 1
    BACKGROUND = 2


# Default priority per event type. Every Events.* value MUST appear
# here so `get_priority()` can find it without a fallback. The test
# suite enforces this invariant with a coverage check.
#
# Rationale per event:
#   - Plugin / game lifecycle → CRITICAL (affects session tracking,
#     CDP state, and user-visible UI transitions)
#   - Auth and sync start/complete → NORMAL (user-triggered)
#   - Progress and store errors → BACKGROUND (idempotent or non-
#     fatal; safe to drop under pressure)
_DEFAULT_PRIORITY: dict[Events, EventPriority] = {
    # ── Plugin lifecycle ─────────────────────────────────────
    Events.PLUGIN_LOADED:       EventPriority.CRITICAL,
    Events.PLUGIN_UNLOADING:    EventPriority.CRITICAL,

    # ── Game lifecycle ───────────────────────────────────────
    Events.GAME_LAUNCHED:       EventPriority.CRITICAL,
    Events.GAME_STOPPED:        EventPriority.CRITICAL,
    Events.GAME_INSTALLED:      EventPriority.NORMAL,
    Events.GAME_UNINSTALLED:    EventPriority.NORMAL,
    Events.GAME_UPDATE_AVAILABLE: EventPriority.BACKGROUND,
    Events.PLAYTIME_UPDATED:    EventPriority.BACKGROUND,

    # ── Power/Sleep lifecycle ────────────────────────────────
    Events.SUSPEND:             EventPriority.CRITICAL,
    Events.RESUME:              EventPriority.CRITICAL,

    # ── Sync lifecycle ───────────────────────────────────────
    Events.SYNC_STARTED:        EventPriority.NORMAL,
    Events.SYNC_COMPLETE:       EventPriority.NORMAL,
    Events.SYNC_CANCELLED:      EventPriority.NORMAL,
    Events.SYNC_FAILED:         EventPriority.NORMAL,
    Events.SYNC_PROGRESS:       EventPriority.BACKGROUND,
    Events.SYNC_SKIPPED:        EventPriority.NORMAL,
    Events.SYNC_DEDUP:          EventPriority.NORMAL,

    # ── Store auth lifecycle ─────────────────────────────────
    Events.STORE_AUTH_STARTED:  EventPriority.NORMAL,
    Events.STORE_AUTH_COMPLETE: EventPriority.NORMAL,
    Events.STORE_AUTH_FAILED:   EventPriority.NORMAL,
    Events.STORE_LOGOUT:        EventPriority.NORMAL,
    Events.STORE_REGISTERED:    EventPriority.NORMAL,

    # ── Launcher & Circuit lifecycle ─────────────────────────
    Events.LAUNCHER_STAGE:      EventPriority.CRITICAL,
    Events.CIRCUIT_STATE_CHANGED: EventPriority.NORMAL,

    # ── Download lifecycle ───────────────────────────────────
    Events.DOWNLOAD_QUEUED:     EventPriority.NORMAL,
    Events.DOWNLOAD_STARTED:    EventPriority.NORMAL,
    Events.DOWNLOAD_COMPLETE:   EventPriority.NORMAL,
    Events.DOWNLOAD_CANCELLED:  EventPriority.NORMAL,
    Events.DOWNLOAD_FAILED:     EventPriority.NORMAL,
    Events.DOWNLOAD_PROGRESS:   EventPriority.BACKGROUND,

    # ── Security lifecycle ───────────────────────────────────
    Events.SECURITY_TOKEN_ENCRYPTED:      EventPriority.NORMAL,
    Events.SECURITY_TOKEN_DECRYPTED:      EventPriority.NORMAL,
    Events.SECURITY_DECRYPT_FAILED:       EventPriority.NORMAL,
    Events.SECURITY_TOKEN_FILE_MIGRATED:  EventPriority.NORMAL,
    Events.SECURITY_LEGACY_PLAINTEXT_DETECTED: EventPriority.NORMAL,
    Events.SECURITY_AUTH_FLOW_STARTED:    EventPriority.NORMAL,
    Events.SECURITY_AUTH_FLOW_COMPLETED:  EventPriority.NORMAL,
    Events.SECURITY_AUTH_FLOW_FAILED:     EventPriority.NORMAL,
    Events.SECURITY_TOKEN_AGE_EXCEEDED:   EventPriority.NORMAL,
    Events.SECURITY_PERMISSIONS_CHECK:    EventPriority.NORMAL,
    Events.SECURITY_PERMISSIONS_REPAIRED: EventPriority.NORMAL,
    Events.SECURITY_BRUTEFORCE_SUSPECTED: EventPriority.NORMAL,
    Events.SECURITY_DEVICE_RESET_DETECTED: EventPriority.NORMAL,
    Events.SECURITY_FINGERPRINT_INITIALIZED: EventPriority.NORMAL,
    Events.SECURITY_EXTERNAL_AUTH_CHECK_FAILED: EventPriority.NORMAL,

    # ── Config lifecycle ─────────────────────────────────────
    Events.CONFIG_VALIDATION_COMPLETED:   EventPriority.NORMAL,
    Events.CONFIG_VALIDATION_FAILED:      EventPriority.NORMAL,

    # ── Subscription lifecycle ───────────────────────────────
    Events.SUBSCRIPTION_DETECTED:         EventPriority.NORMAL,
    Events.SUBSCRIPTION_EXPIRED:          EventPriority.NORMAL,
    Events.SUBSCRIPTION_CHECK_FAILED:      EventPriority.NORMAL,

    # ── Account lifecycle ────────────────────────────────────
    Events.ACCOUNT_SWITCHED:              EventPriority.CRITICAL,

    # ── Shortcut & Artwork ───────────────────────────────────
    Events.SHORTCUT_CREATED:              EventPriority.NORMAL,
    Events.ARTWORK_REQUEST:               EventPriority.BACKGROUND,

    # ── Generic ──────────────────────────────────────────────
    Events.STORE_ERROR:                   EventPriority.BACKGROUND,
    Events.RUNTIME_PROBES_REPORTED:        EventPriority.BACKGROUND,
}


# Events whose payloads are idempotent enough to be coalesced in the
# dispatch queue. When a second event of the same (type, coalesce_key)
# arrives before the first has been dispatched, the earlier one is
# replaced in-place. This absorbs thousands of SYNC_PROGRESS ticks
# into a handful of actual dispatches.
#
# Coalescing key is the kwarg name whose value distinguishes otherwise
# identical events — e.g. `store` for SYNC_PROGRESS (one progress
# stream per store), `download_id` for DOWNLOAD_PROGRESS.
COALESCE_KEY: dict[Events, str] = {
    Events.SYNC_PROGRESS:     "store",
    Events.DOWNLOAD_PROGRESS: "download_id",
}


def get_priority(
    event: Events | str,
) -> EventPriority:
    """Return the default priority for an event type.

    Accepts either an Events member or a raw string (for dynamically-
    typed events from legacy code). Unknown events fall back to
    NORMAL — never BACKGROUND — so a forgotten event classification
    cannot silently be dropped under pressure.
    """
    if isinstance(event, Events):
        return _DEFAULT_PRIORITY.get(event, EventPriority.NORMAL)

    # String lookup: try to resolve to Events enum member first
    try:
        resolved = Events(event)
        return _DEFAULT_PRIORITY.get(resolved, EventPriority.NORMAL)
    except ValueError:
        return EventPriority.NORMAL


def get_coalesce_key(
    event: Events | str,
) -> str:
    """Return the coalesce-key kwarg name for an event, or '' if none.

    An empty string means "do not coalesce" — each emission is kept
    as a distinct queue entry. A non-empty value means "replace any
    pending event of the same (type, kwargs[key]) pair in the queue".
    """
    if isinstance(event, Events):
        return COALESCE_KEY.get(event, "")
    try:
        resolved = Events(event)
        return COALESCE_KEY.get(resolved, "")
    except ValueError:
        return ""
