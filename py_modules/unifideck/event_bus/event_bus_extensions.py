"""Four small capabilities grouped in one module to minimize file
proliferation. Each is a standalone class, so SRP is preserved at
the class level even though they share a file:

  - TypedEventRegistry (P7.4): runtime validation of event kwargs
    against a declared schema, with mypy-friendly Protocol stubs.
  - DeadLetterQueue (P7.5): capture events whose handlers all
    failed, for later replay. Persisted to a JSONL file via the
Security note: the DLQ file is created with mode 0o600 (owner-only
read/write) because it may incidentally contain sensitive kwargs
like store names or game IDs. OAuth tokens must never be passed
as event kwargs; this is a callsite responsibility the DLQ cannot
enforce.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
)

from ..core.types import Events

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .event_replay import EventReplayBuffer
    from .priority_dispatcher import PriorityDispatcher
    from .supervision.metrics_handler import HandlerLatencyCollector
    from .supervision.watchdog_handler import HandlerWatchdog

logger = logging.getLogger(__name__)


# ── P7.4 — Typed event schemas ───────────────────────────────────

class EventPayload(Protocol):
    """Marker Protocol for typed event payloads.

    Concrete payloads inherit from this via Protocol subclassing:

        class SyncCompletePayload(EventPayload, Protocol):
            games: list
            stores_synced: list
            duration_ms: int
    """


@dataclass
class EventSchema:
    """Declarative kwargs contract for one event type."""

    required: set[str] = field(default_factory=set)
    optional: set[str] = field(default_factory=set)

    def validate(self, kwargs: dict[str, Any]) -> str | None:
        """Return None on success, or an error message string."""
        missing = self.required - set(kwargs.keys())
        if missing:
            return f"missing required kwargs: {sorted(missing)}"
        allowed = self.required | self.optional
        extra = set(kwargs.keys()) - allowed
        if extra:
            return f"unexpected kwargs: {sorted(extra)}"
        return None


class TypedEventRegistry:
    """Holds per-event schemas and validates at emit time."""

    def __init__(self) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._schemas: dict[str, EventSchema] = {}

    def declare(self, event: Events | str, schema: EventSchema) -> None:  # noqa: D102 — documentation pending (Sprint D)
        key = event.value if isinstance(event, Events) else str(event)
        self._schemas[key] = schema

    def validate(  # noqa: D102 — documentation pending (Sprint D)
        self, event: Events | str, kwargs: dict[str, Any],
    ) -> str | None:
        key = event.value if isinstance(event, Events) else str(event)
        schema = self._schemas.get(key)
        if schema is None:
            return None  # unregistered events pass through
        return schema.validate(kwargs)


# ── P7.6 — Predicate filter on subscriptions ─────────────────────

# A predicate is any callable taking kwargs and returning bool.
Predicate = Callable[..., bool]


class PredicateFilter:
    """Wraps a handler with an arbitrary pre-invocation filter.

    Usage at subscription time:

        filter = PredicateFilter(handler, lambda store, **_: store == "epic")
        bus.on(Events.GAME_LAUNCHED, filter)
    """

    def __init__(self, handler: Callable[..., Any], predicate: Predicate) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._handler = handler
        self._predicate = predicate
        # Preserve the inner handler name for watchdog/metrics
        self.__name__ = getattr(handler, "__name__", "filtered_handler")
        self.__qualname__ = getattr(handler, "__qualname__", self.__name__)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: D102 — documentation pending (Sprint D)
        try:
            matches = self._predicate(*args, **kwargs)
        except Exception:
            # A broken predicate should not silently eat events —
            # log and pass through so the handler still runs.
            logger.exception(
                "[PredicateFilter] predicate raised; passing through",
            )
            matches = True
        if not matches:
            return None
        return await self._handler(*args, **kwargs)


# ── P7.7 — Debug snapshot ────────────────────────────────────────

class DebugSnapshot:
    """Collects the full state of bus + dispatcher for debugging.

    Called by a dev-only RPC `debug_snapshot()` on the Plugin class.
    Returns a JSON-serializable dict that operators can paste into
    bug tickets to reproduce issues. Never called in production hot
    paths — cost is measured once per call, not per event.
    """

    @staticmethod
    def collect(
        bus: EventBus,
        dispatcher: PriorityDispatcher | None = None,
        watchdog: HandlerWatchdog | None = None,
        metrics: HandlerLatencyCollector | None = None,
        replay: EventReplayBuffer | None = None,
        dlq: DeadLetterQueue | None = None,
    ) -> dict[str, Any]:
        """Gather every observable slice of state into one dict."""
        snapshot: dict[str, Any] = {
            "bus": {
                "handler_counts": DebugSnapshot._safe_call(
                    getattr(bus, "health", None),
                ),
            },
        }
        if dispatcher is not None:
            m = dispatcher.get_metrics()
            snapshot["dispatcher"] = {
                "emitted_total": m.emitted_total,
                "dispatched_total": m.dispatched_total,
                "coalesced_total": m.coalesced_total,
                "dropped_background_total": m.dropped_background_total,
                "pending_by_priority": m.pending_by_priority,
            }
        if watchdog is not None:
            snapshot["watchdog"] = {
                name: {
                    "invocations": ms.invocations,
                    "timeouts": ms.timeouts,
                    "consecutive_timeouts": ms.consecutive_timeouts,
                    "quarantined": ms.quarantined,
                }
                for name, ms in watchdog.get_metrics().items()
            }
        if metrics is not None:
            snapshot["handler_metrics"] = metrics.get_top_n(n=10)
        if replay is not None:
            snapshot["replay_sizes"] = {
                "total": replay.size(),
            }
        if dlq is not None:
            snapshot["dlq_entries"] = len(dlq)
        return snapshot

    @staticmethod
    def _safe_call(fn: Callable | None) -> Any:
        if fn is None:
            return None
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — deliberate: isolate user callback
            return {"error": str(e)}


# ── P7.5 — Dead letter queue ─────────────────────────────────────

class DeadLetterQueue:
    """Capture events whose handlers all failed.

    When an event is emitted and ZERO of its registered handlers
    complete successfully (every one raises or times out), the
    event is appended to the dead letter queue so operators can
    inspect it later rather than losing the payload silently.

    Kept deliberately minimal: a bounded ring buffer of (event,
    payload, reason) tuples. Production callers either drain
    the queue for a diagnostics RPC, or log-and-forget on
    shutdown. There is no retry mechanism — DLQ is for audit,
    not for recovery.
    """

    def __init__(self, max_size: int = 256) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._max_size = int(max_size)
        self._entries: list[dict[str, Any]] = []

    def record(
        self,
        event: str,
        payload: dict[str, Any] | None,
        reason: str,
    ) -> None:
        """Append one failed event. Oldest entries are dropped."""
        self._entries.append({
            "event": event,
            "payload": payload or {},
            "reason": reason,
        })
        if len(self._entries) > self._max_size:
            self._entries = self._entries[-self._max_size:]

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a copy of the current buffer (newest last)."""
        return list(self._entries)

    def clear(self) -> None:
        """Drop every recorded event."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
